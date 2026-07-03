"""Shared helpers for the Saxon ROR subset scripts.

Deliberately minimal: standard library only (``urllib``), no third-party
dependencies. Used by ``update_ror.py``, ``update_openalex.py`` and
``backfill_history.py``.

The Research Organization Registry (ROR) publishes its full data dump on
Zenodo under the concept DOI ``10.5281/zenodo.6347574``. Each release ships a
ZIP archive. Depending on the release era the archive contains ROR records in
the v1 schema, the v2 schema, or (during the 2024 transition) both. This module
detects the schema, filters the dump to organizations located in Saxony
(Sachsen), and writes the curated subset without modifying any record.
"""

from __future__ import annotations

import csv
import io
import json
import time
import unicodedata
import urllib.parse
import urllib.request
import zipfile
from datetime import date, datetime, timezone
from pathlib import Path

# --- Constants ---------------------------------------------------------------

# Zenodo concept record for "ROR Data". Always resolve to the latest version.
ZENODO_CONCEPT_ID = "6347574"
ZENODO_CONCEPT_DOI = "10.5281/zenodo.6347574"

# GeoNames admin1 identifier for the Free State of Saxony. Present in the v1
# schema only; the v2 schema dropped the admin1 GeoNames id.
SAXONY_ADMIN1_ID = 2842566
# ISO 3166-2 subdivision code / name used as the v2 filter and the v1 fallback.
SAXONY_SUBDIVISION_CODE = "SN"
SAXONY_SUBDIVISION_NAME = "Saxony"
GERMANY_COUNTRY_CODE = "DE"

USER_AGENT = "saxon-ror/1.0 (+https://github.com/slub/saxon-ror)"

# Repository layout (resolved relative to this file).
REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"
RECORDS_DIR = DATA_DIR / "records"
REUSE_DIR = DATA_DIR / "reuse"
META_PATH = DATA_DIR / "meta.json"


# --- HTTP --------------------------------------------------------------------


def _request(url: str, accept: str | None = None) -> urllib.request.Request:
    headers = {"User-Agent": USER_AGENT}
    if accept:
        headers["Accept"] = accept
    return urllib.request.Request(url, headers=headers)


def http_json(url: str, retries: int = 4, backoff: float = 2.0) -> dict:
    """GET a URL and parse JSON, with simple exponential backoff retries."""
    last = None
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(
                _request(url, accept="application/json"), timeout=120
            ) as resp:
                return json.load(resp)
        except Exception as exc:  # noqa: BLE401 - retry on any transient error
            last = exc
            if attempt < retries - 1:
                time.sleep(backoff * (attempt + 1))
    raise RuntimeError(f"GET {url} failed after {retries} attempts: {last}")


def download(url: str, dest: Path, retries: int = 3, backoff: float = 3.0) -> Path:
    """Download a (potentially large) file to ``dest`` in a streaming fashion."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    last = None
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(_request(url), timeout=600) as resp, open(
                dest, "wb"
            ) as out:
                while True:
                    chunk = resp.read(1 << 20)
                    if not chunk:
                        break
                    out.write(chunk)
            return dest
        except Exception as exc:  # noqa: BLE401
            last = exc
            if attempt < retries - 1:
                time.sleep(backoff * (attempt + 1))
    raise RuntimeError(f"Download {url} failed after {retries} attempts: {last}")


# --- Zenodo ------------------------------------------------------------------


def _zip_file_entry(record: dict) -> dict:
    """Return the ZIP file entry of a Zenodo record."""
    files = record.get("files", [])
    zips = [f for f in files if str(f.get("key", "")).endswith(".zip")]
    if not zips:
        raise RuntimeError(f"Zenodo record {record.get('id')} has no .zip file")
    return zips[0]


def zenodo_latest() -> dict:
    """Resolve the concept DOI to the latest published version.

    Returns a dict with keys: ``id``, ``version``, ``doi``, ``publication_date``,
    ``created``, ``file_name``, ``download_url``.
    """
    rec = http_json(f"https://zenodo.org/api/records/{ZENODO_CONCEPT_ID}")
    return _summarize_version(rec)


def zenodo_all_versions() -> list[dict]:
    """Return every published version of the concept, oldest first."""
    versions: list[dict] = []
    # The /versions listing requires a concrete version record id, not the
    # concept id. Resolve the concept to its latest version first.
    latest = http_json(f"https://zenodo.org/api/records/{ZENODO_CONCEPT_ID}")
    url = f"https://zenodo.org/api/records/{latest['id']}/versions"
    while url:
        page = http_json(url)
        for rec in page.get("hits", {}).get("hits", []):
            try:
                versions.append(_summarize_version(rec))
            except RuntimeError:
                # Skip malformed/embargoed records without a zip.
                continue
        url = page.get("links", {}).get("next")
        if url:
            time.sleep(0.3)
    versions.sort(key=lambda v: (v["publication_date"], v["version"]))
    return versions


def _summarize_version(rec: dict) -> dict:
    meta = rec.get("metadata", {})
    entry = _zip_file_entry(rec)
    return {
        "id": rec.get("id"),
        "version": meta.get("version"),
        "doi": rec.get("doi"),
        "publication_date": meta.get("publication_date"),
        # Real ISO datetime the version record was created on Zenodo, e.g.
        # ``2026-06-23T17:56:38.985576+00:00`` -- used to date commits at the
        # true publication moment rather than a placeholder time of day.
        "created": rec.get("created"),
        "file_name": entry.get("key"),
        "download_url": entry.get("links", {}).get("self"),
    }


# --- Schema detection & extraction -------------------------------------------


def load_saxon_subset(zip_path: Path) -> tuple[list[dict], str]:
    """Load a dump ZIP, filter to Saxony, and report the storage schema.

    Returns ``(saxon_records, storage_schema)``.

    A dump ZIP may contain a v1 JSON, a v2 JSON, or both (during the 2024/25
    transition). The two schemas locate an organization very differently, and --
    crucially -- the *early* v2 files (v1.45 .. ~v1.50) omit the
    ``country_subdivision_*`` fields entirely, so they cannot be filtered on
    their own. To stay correct across every era we:

    * compute the set of Saxon ROR ids from **whichever** file carries usable
      location detail -- v1 via the GeoNames admin1 id, v2 via the subdivision
      fields -- and take the union;
    * store the records in the **preferred** schema (v2 when a v2 file is
      present, otherwise v1), selecting those whose id is in the Saxon set.

    This works because a v1 file (with reliable admin1 ids) is always present
    whenever the v2 file lacks subdivision fields; once ROR ships a v2-only dump,
    that v2 file carries subdivisions again.
    """
    v1_records: list[dict] | None = None
    v2_records: list[dict] | None = None
    with zipfile.ZipFile(zip_path) as zf:
        members = [
            n
            for n in zf.namelist()
            if n.endswith(".json") and not n.startswith("__MACOSX")
        ]
        for member in members:
            records = json.loads(zf.read(member))
            if not records:
                continue
            schema = detect_schema(records)
            if schema == "v1":
                v1_records = records
            else:
                v2_records = records

    saxon_ids: set[str] = set()
    if v1_records is not None:
        saxon_ids |= {r.get("id") for r in v1_records if _is_saxon_v1(r)}
    if v2_records is not None:
        saxon_ids |= {r.get("id") for r in v2_records if _is_saxon_v2(r)}

    if v2_records is not None:
        storage_schema, source = "v2", v2_records
    elif v1_records is not None:
        storage_schema, source = "v1", v1_records
    else:
        raise RuntimeError(f"No ROR records found in {zip_path}")

    subset = [r for r in source if r.get("id") in saxon_ids]
    return subset, storage_schema


def detect_schema(records: list[dict]) -> str:
    if not records:
        raise RuntimeError("Dump contained zero records")
    sample = records[0]
    if "locations" in sample:
        return "v2"
    if "addresses" in sample:
        return "v1"
    raise RuntimeError("Unable to detect ROR schema version from record shape")


# --- Saxon filter ------------------------------------------------------------


def _is_saxon_v2(record: dict) -> bool:
    for loc in record.get("locations", []):
        g = loc.get("geonames_details", {}) or {}
        if g.get("country_code") != GERMANY_COUNTRY_CODE:
            continue
        if (
            g.get("country_subdivision_name") == SAXONY_SUBDIVISION_NAME
            or g.get("country_subdivision_code") == SAXONY_SUBDIVISION_CODE
        ):
            return True
    return False


def _is_saxon_v1(record: dict) -> bool:
    for addr in record.get("addresses", []):
        admin1 = (addr.get("geonames_city", {}) or {}).get("geonames_admin1", {}) or {}
        if admin1.get("id") == SAXONY_ADMIN1_ID:
            return True
    # Fallback: state code DE-SN / state name "Saxony" for a German record.
    country_code = (record.get("country", {}) or {}).get("country_code")
    if country_code == GERMANY_COUNTRY_CODE:
        for addr in record.get("addresses", []):
            if addr.get("state_code") == "DE-SN" or addr.get("state") == "Saxony":
                return True
    return False


# --- Record field extraction (schema-aware) ----------------------------------


def ror_id(record: dict) -> str:
    return record.get("id", "")


def ror_suffix(record: dict) -> str:
    """The final path segment of a ROR id, e.g. ``042aqky30``."""
    return ror_id(record).rstrip("/").rsplit("/", 1)[-1]


def display_name(record: dict, schema: str) -> str:
    if schema == "v2":
        for n in record.get("names", []):
            if "ror_display" in (n.get("types") or []):
                return n.get("value", "")
        names = record.get("names", [])
        return names[0].get("value", "") if names else ror_suffix(record)
    return record.get("name", "") or ror_suffix(record)


def acronyms(record: dict, schema: str) -> list[str]:
    if schema == "v2":
        return [
            n.get("value", "")
            for n in record.get("names", [])
            if "acronym" in (n.get("types") or [])
        ]
    return list(record.get("acronyms", []) or [])


def city(record: dict, schema: str) -> str:
    if schema == "v2":
        for loc in record.get("locations", []):
            name = (loc.get("geonames_details", {}) or {}).get("name")
            if name:
                return name
        return ""
    for addr in record.get("addresses", []):
        if addr.get("city"):
            return addr["city"]
    return ""


def types(record: dict, schema: str) -> list[str]:
    return list(record.get("types", []) or [])


def status(record: dict) -> str:
    return record.get("status", "") or ""


def wikidata_id(record: dict, schema: str) -> str:
    if schema == "v2":
        for ext in record.get("external_ids", []):
            if ext.get("type") == "wikidata":
                return ext.get("preferred") or (ext.get("all") or [""])[0]
        return ""
    wd = (record.get("external_ids", {}) or {}).get("Wikidata", {}) or {}
    all_ids = wd.get("all") or []
    if isinstance(all_ids, str):
        all_ids = [all_ids]
    return wd.get("preferred") or (all_ids[0] if all_ids else "")


def website(record: dict, schema: str) -> str:
    if schema == "v2":
        for link in record.get("links", []):
            if link.get("type") == "website":
                return link.get("value", "")
        return ""
    links = record.get("links", []) or []
    return links[0] if links else ""


def alt_names(record: dict, schema: str) -> list[str]:
    """Alternative names: labels + aliases, minus the display name and acronyms."""
    display = display_name(record, schema)
    out: list[str] = []
    if schema == "v2":
        for n in record.get("names", []):
            kinds = n.get("types") or []
            if "ror_display" in kinds or "acronym" in kinds:
                continue
            if "label" in kinds or "alias" in kinds:
                out.append(n.get("value", ""))
    else:
        for label in record.get("labels", []) or []:
            out.append(label.get("label", "") if isinstance(label, dict) else label)
        out.extend(record.get("aliases", []) or [])
    return _dedupe(v for v in out if v and v != display)


def established(record: dict) -> str:
    value = record.get("established")
    return str(value) if value not in (None, "") else ""


def created_date(record: dict, schema: str) -> str:
    if schema == "v2":
        return (record.get("admin", {}) or {}).get("created", {}).get("date", "") or ""
    return ""  # v1 dumps carry no admin block


def modified_date(record: dict, schema: str) -> str:
    if schema == "v2":
        admin = record.get("admin", {}) or {}
        return (admin.get("last_modified", {}) or {}).get("date", "") or ""
    return ""


def _external_ids_all(record: dict, schema: str, v2_type: str, v1_key: str) -> list[str]:
    """All values of one external-id type (v2 list vs v1 dict shapes)."""
    if schema == "v2":
        for ext in record.get("external_ids", []):
            if ext.get("type") == v2_type:
                return _dedupe(_as_list(ext.get("all")) + _as_list(ext.get("preferred")))
        return []
    entry = (record.get("external_ids", {}) or {}).get(v1_key, {}) or {}
    return _dedupe(_as_list(entry.get("all")) + _as_list(entry.get("preferred")))


def isni(record: dict, schema: str) -> list[str]:
    return _external_ids_all(record, schema, "isni", "ISNI")


def fundref(record: dict, schema: str) -> list[str]:
    return _external_ids_all(record, schema, "fundref", "FundRef")


def wikidata_ids(record: dict, schema: str) -> list[str]:
    return _external_ids_all(record, schema, "wikidata", "Wikidata")


def cities(record: dict, schema: str) -> list[str]:
    """All distinct cities across the record's locations/addresses."""
    out: list[str] = []
    if schema == "v2":
        for loc in record.get("locations", []):
            name = (loc.get("geonames_details", {}) or {}).get("name")
            if name:
                out.append(name)
    else:
        for addr in record.get("addresses", []):
            if addr.get("city"):
                out.append(addr["city"])
    return _dedupe(out)


# The five ROR relationship types, always emitted as columns (in this order) so
# the CSV header stays stable across dumps even when a type is absent.
RELATIONSHIP_TYPES = ["parent", "child", "related", "predecessor", "successor"]


def relationships_by_type(record: dict, schema: str) -> dict[str, list[str]]:
    """Map each relationship type -> list of related ROR id suffixes."""
    out: dict[str, list[str]] = {t: [] for t in RELATIONSHIP_TYPES}
    for rel in record.get("relationships", []) or []:
        rtype = (rel.get("type") or "").lower()  # v1 capitalizes, v2 lowercases
        if rtype in out:
            out[rtype].append(_suffix(rel.get("id", "")))
    return {t: _dedupe(v) for t, v in out.items()}


def _suffix(ror_url: str) -> str:
    return (ror_url or "").rstrip("/").rsplit("/", 1)[-1]


def _as_list(value) -> list[str]:
    if value in (None, ""):
        return []
    return [value] if isinstance(value, str) else list(value)


def _dedupe(values) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for v in values:
        if v and v not in seen:
            seen.add(v)
            out.append(v)
    return out


# --- Writers -----------------------------------------------------------------


def dump_json(obj, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2, sort_keys=False)
        fh.write("\n")


def write_subset(records: list[dict], schema: str, data_dir: Path = DATA_DIR) -> None:
    """Write records/<suffix>.json, records.json and records.csv.

    Records are stored unmodified (pretty-printed, field order preserved). The
    per-record directory is rebuilt from scratch so removed organizations do not
    linger as orphan files.
    """
    records = sorted(records, key=lambda r: ror_suffix(r))
    records_dir = data_dir / "records"

    # Rebuild the per-record directory.
    if records_dir.exists():
        for stale in records_dir.glob("*.json"):
            stale.unlink()
    records_dir.mkdir(parents=True, exist_ok=True)

    for rec in records:
        dump_json(rec, records_dir / f"{ror_suffix(rec)}.json")

    dump_json(records, data_dir / "records.json")
    write_csv(records, schema, data_dir / "records.csv")


# Multi-value cells join every value with this delimiter. Verified safe: no ROR
# name, id, type or url in the subset contains it.
CSV_DELIMITER = "; "

CSV_FIELDS = [
    "ror_id",
    "display_name",
    "acronyms",
    "alt_names",
    "types",
    "city",
    "status",
    "established",
    "created",
    "modified",
    "isni",
    "fundref",
    "wikidata_id",
    "website",
    "parents",
    "children",
    "related",
    "predecessors",
    "successors",
]


def write_csv(records: list[dict], schema: str, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    def join(values) -> str:
        return CSV_DELIMITER.join(values)

    with open(path, "w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for rec in sorted(records, key=lambda r: display_name(r, schema).lower()):
            rels = relationships_by_type(rec, schema)
            writer.writerow(
                {
                    "ror_id": ror_suffix(rec),
                    "display_name": display_name(rec, schema),
                    "acronyms": join(acronyms(rec, schema)),
                    "alt_names": join(alt_names(rec, schema)),
                    "types": join(types(rec, schema)),
                    "city": join(cities(rec, schema)),
                    "status": status(rec),
                    "established": established(rec),
                    "created": created_date(rec, schema),
                    "modified": modified_date(rec, schema),
                    "isni": join(isni(rec, schema)),
                    "fundref": join(fundref(rec, schema)),
                    "wikidata_id": join(wikidata_ids(rec, schema)),
                    "website": website(rec, schema),
                    "parents": join(rels["parent"]),
                    "children": join(rels["child"]),
                    "related": join(rels["related"]),
                    "predecessors": join(rels["predecessor"]),
                    "successors": join(rels["successor"]),
                }
            )


# --- meta.json ---------------------------------------------------------------


def read_meta(data_dir: Path = DATA_DIR) -> dict:
    path = data_dir / "meta.json"
    if path.exists():
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    return {}


def write_meta(meta: dict, data_dir: Path = DATA_DIR) -> None:
    dump_json(meta, data_dir / "meta.json")


def today_iso() -> str:
    return datetime.now(timezone.utc).date().isoformat()


# --- Validation --------------------------------------------------------------


def normalize(text: str) -> str:
    """Casefold and strip diacritics for deterministic, accent-insensitive match."""
    decomposed = unicodedata.normalize("NFKD", text)
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return stripped.casefold()


def validate_pairing(data_dir: Path = DATA_DIR) -> list[str]:
    """Check records/ <-> records.json and reuse/*/records/ pairing.

    Returns a list of human-readable problems (empty when consistent).
    """
    problems: list[str] = []
    records_json = data_dir / "records.json"
    if not records_json.exists():
        return [f"missing {records_json}"]

    with open(records_json, encoding="utf-8") as fh:
        combined = json.load(fh)
    combined_suffixes = {ror_suffix(r) for r in combined}

    file_suffixes = {p.stem for p in (data_dir / "records").glob("*.json")}
    for missing in sorted(combined_suffixes - file_suffixes):
        problems.append(f"records.json entry {missing} has no per-record file")
    for orphan in sorted(file_suffixes - combined_suffixes):
        problems.append(f"records/{orphan}.json has no records.json entry")

    reuse = data_dir / "reuse"
    if reuse.exists():
        for source_dir in sorted(reuse.iterdir()):
            recs = source_dir / "records"
            if not recs.is_dir():
                continue
            for p in recs.glob("*.json"):
                if p.stem not in combined_suffixes:
                    problems.append(
                        f"reuse/{source_dir.name}/records/{p.stem}.json "
                        "has no matching ROR record (orphan)"
                    )
    return problems
