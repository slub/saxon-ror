#!/usr/bin/env python3
"""Derive the re3data companion layer for the Saxon ROR subset.

Reads re3data's full daily XML export (schema 4.0, one file per repository)
and links a repository to a record of ``data/records.json`` only where one of
its ``institution`` entries carries an identifier of type ``ROR`` with that
record's id. Names, other identifiers and ROR relationships are never used.
Responsibility types and dates come from the matching entry itself, never from
a sibling entry. Filtering the complete export keeps misses independent of any
search index; a miss means "no explicit ROR link", not "no repository".

Output, keyed by ROR id suffix like every companion:

    data/reuse/re3data/records/<ror-id-suffix>.json   projection per linked record
    data/reuse/re3data/records.json                   the combined array
    data/reuse/re3data/sources/<re3data-id>.xml       export bytes, unchanged

plus ``reuse.re3data`` in ``data/meta.json``.

Any download, ZIP, XML, schema or consistency error aborts before ``data/`` is
touched. A run that would drop every match after a run that had some is refused
unless ``--allow-empty`` is given -- a broken export is the likelier cause.

Usage:
    python scripts/update_re3data.py                  # download and update
    python scripts/update_re3data.py --input export.zip [--retrieved YYYY-MM-DD]
    python scripts/update_re3data.py --check          # validate committed layer
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import tempfile
import time
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from email.utils import parsedate_to_datetime
from pathlib import Path

import ror_lib as R

SOURCE_NAME = "re3data"
EXPORT_URL = "https://www.re3data.org/export/re3data-repositories.zip"
REPOSITORY_URL = "https://www.re3data.org/repository/{}"

# Any other namespace aborts the run rather than silently matching nothing.
SCHEMA_NAMESPACE = "http://www.re3data.org/schema/4-0"
SCHEMA_VERSION = "4.0"
SCHEMA_DOC = "https://doi.org/10.48440/re3.014"
NS = f"{{{SCHEMA_NAMESPACE}}}"

# Version of our JSON projection (see project_repository).
PROJECTION_VERSION = 1

# Floors below which the export is treated as broken (October 2026: 3,539
# repositories, 5,325 ROR-typed identifiers).
MIN_EXPORT_REPOSITORIES = 1000
MIN_EXPORT_ROR_IDENTIFIERS = 500

# Members are read into memory, never extracted; these bound that.
MAX_MEMBER_BYTES = 5 * 1024 * 1024
MAX_TOTAL_BYTES = 512 * 1024 * 1024

RE3DATA_ID_RE = re.compile(r"r3d\d{9}")

# ROR ids: "0", six Crockford base32 characters, two checksum digits.
_CROCKFORD = "0123456789abcdefghjkmnpqrstvwxyz"
_ROR_ID_RE = re.compile(r"0[0-9a-hjkmnp-tv-z]{6}[0-9]{2}")
# Accepted wrappings: http(s), www., bare ``ror.org/<id>`` or id, whitespace,
# trailing slash, and the redundant prefixes found in the export
# ("ror.org/ror.org/<id>", "ror.org/ROR:<id>", "ror.org/ <id>"). Anything else
# is rejected and listed in meta.json.
_ROR_VALUE_RE = re.compile(
    r"(?:(?:https?://)?(?:www\.)?ror\.org/)?"
    r"(?:ror\.org/|ror:)?\s*"
    r"(?P<id>[0-9a-z]{9})/?",
    re.I,
)


class ExportError(RuntimeError):
    """The export cannot be used; nothing must be written."""


# --- ROR identifiers ---------------------------------------------------------


def ror_checksum_ok(rid: str) -> bool:
    """ISO 7064 Mod 97-10 check of a ROR id's last two digits."""
    number = 0
    for char in rid[:7]:
        number = number * 32 + _CROCKFORD.index(char)
    return 98 - (number * 100) % 97 == int(rid[7:])


def normalize_ror(value: str | None) -> str | None:
    """Canonical ``https://ror.org/<id>``, or ``None`` unless the id is
    lowercase, well-formed and checksum-valid."""
    if value is None:
        return None
    m = _ROR_VALUE_RE.fullmatch(value.strip())
    if not m:
        return None
    rid = m.group("id")
    if not _ROR_ID_RE.fullmatch(rid) or not ror_checksum_ok(rid):
        return None
    return f"https://ror.org/{rid}"


# --- Parsing -----------------------------------------------------------------


def _text(elem: ET.Element | None) -> str | None:
    """Stripped element text, ``None`` when absent or blank."""
    if elem is None or elem.text is None:
        return None
    value = elem.text.strip()
    return value or None


def _lang(elem: ET.Element | None) -> str | None:
    return elem.get("language") if elem is not None else None


def parse_repository(data: bytes, member: str) -> dict:
    """Parse one export member into the fields this layer needs.

    Institutions keep their 1-based ``position`` among the ``institution``
    elements, so the projection points back to the exact source entry.
    """
    # No DTD belongs in the export; refusing it rules out entity expansion.
    if b"<!DOCTYPE" in data[:2048].upper():
        raise ExportError(f"{member}: unexpected DOCTYPE declaration")
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise ExportError(f"{member}: invalid XML ({exc})") from exc

    if root.tag != f"{NS}re3data":
        raise ExportError(
            f"{member}: unexpected root element {root.tag!r}; "
            f"expected re3data in namespace {SCHEMA_NAMESPACE}"
        )
    repos = root.findall(f"{NS}repository")
    if len(repos) != 1:
        raise ExportError(f"{member}: expected one repository element, found {len(repos)}")
    rep = repos[0]

    ids = [_text(e) for e in rep.findall(f"{NS}identifiers/{NS}re3data")]
    if len(ids) != 1 or not ids[0] or not RE3DATA_ID_RE.fullmatch(ids[0]):
        raise ExportError(f"{member}: missing or malformed re3data id {ids!r}")
    rid = ids[0]

    name_el = rep.find(f"{NS}repositoryName")
    name = _text(name_el)
    if not name:
        raise ExportError(f"{member} ({rid}): missing repositoryName")

    end = rep.find(f"{NS}endDate")
    institutions = []
    ror_values = []
    for pos, inst in enumerate(rep.findall(f"{NS}institution"), start=1):
        iname_el = inst.find(f"{NS}institutionName")
        rors: list[str] = []
        for ident in inst.findall(f"{NS}institutionIdentifier"):
            itype = (_text(ident.find(f"{NS}institutionIdentifierType")) or "").casefold()
            if itype != "ror":
                continue
            raw = ident.findtext(f"{NS}institutionIdentifierUrl")
            canonical = normalize_ror(raw)
            ror_values.append({"value": raw, "canonical": canonical})
            if canonical and canonical not in rors:
                rors.append(canonical)
        institutions.append(
            {
                "position": pos,
                "name": _text(iname_el),
                "name_language": _lang(iname_el),
                "ror_ids": rors,
                "responsibility_types": [
                    v for v in (_text(e) for e in inst.findall(f"{NS}responsibilityType")) if v
                ],
                "responsibility_start_date": _text(inst.find(f"{NS}responsibilityStartDate")),
                "responsibility_end_date": _text(inst.find(f"{NS}responsibilityEndDate")),
            }
        )

    return {
        "id": rid,
        "name": name,
        "name_language": _lang(name_el),
        "url": _text(rep.find(f"{NS}repositoryUrl")),
        "doi": _text(rep.find(f"{NS}identifiers/{NS}doi")),
        "types": [v for v in (_text(e) for e in rep.findall(f"{NS}type")) if v],
        "start_date": _text(rep.find(f"{NS}startDate")),
        "closed_date": _text(end.find(f"{NS}closed")) if end is not None else None,
        "offline_date": _text(end.find(f"{NS}offline")) if end is not None else None,
        "entry_date": _text(rep.find(f"{NS}entryDate")),
        "last_update": _text(rep.find(f"{NS}lastUpdate")),
        "institutions": institutions,
        "ror_values": ror_values,
    }


def read_export(zip_path: Path) -> tuple[dict[str, dict], dict[str, bytes], dict]:
    """Parse every repository in the export ZIP.

    Returns ``(repositories_by_id, raw_xml_by_id, stats)``. Archive paths are
    used in error messages only, never as output paths.
    """
    try:
        zf = zipfile.ZipFile(zip_path)
    except (zipfile.BadZipFile, OSError) as exc:
        raise ExportError(f"{zip_path}: not a readable ZIP archive ({exc})") from exc

    repos: dict[str, dict] = {}
    raw: dict[str, bytes] = {}
    total = 0
    ror_identifiers = 0
    with zf:
        members = [i for i in zf.infolist() if not i.is_dir()]
        for info in members:
            name = info.filename
            if not name.lower().endswith(".xml") or name.startswith("__MACOSX/"):
                raise ExportError(f"unexpected member in export: {name!r}")
            if info.file_size > MAX_MEMBER_BYTES:
                raise ExportError(f"{name}: member too large ({info.file_size} bytes)")
            total += info.file_size
            if total > MAX_TOTAL_BYTES:
                raise ExportError("export exceeds the uncompressed size limit")
            try:
                data = zf.read(info)  # verifies the member's CRC
            except (zipfile.BadZipFile, OSError, EOFError) as exc:
                raise ExportError(f"{name}: unreadable member ({exc})") from exc
            repo = parse_repository(data, name)
            if repo["id"] in repos:
                raise ExportError(f"{name}: duplicate re3data id {repo['id']}")
            repos[repo["id"]] = repo
            raw[repo["id"]] = data
            ror_identifiers += len(repo["ror_values"])

    if len(repos) < MIN_EXPORT_REPOSITORIES:
        raise ExportError(
            f"export holds only {len(repos)} repositories "
            f"(expected at least {MIN_EXPORT_REPOSITORIES})"
        )
    if ror_identifiers < MIN_EXPORT_ROR_IDENTIFIERS:
        raise ExportError(
            f"export holds only {ror_identifiers} ROR-typed institution identifiers "
            f"(expected at least {MIN_EXPORT_ROR_IDENTIFIERS}) -- schema change?"
        )
    return repos, raw, {"repositories": len(repos), "ror_identifiers": ror_identifiers}


# --- Matching ----------------------------------------------------------------


def match(repos: dict[str, dict], ror_ids: list[str]) -> dict[str, dict]:
    """Build ``{ror_id: record}`` for every matched ROR id.

    Many-to-many. A repository appears once per organization and keeps every
    institution entry carrying that organization's ROR id.
    """
    wanted = set(ror_ids)
    out: dict[str, dict] = {}
    for rid in sorted(repos):
        repo = repos[rid]
        entries_by_ror: dict[str, list[dict]] = {}
        for inst in repo["institutions"]:
            for ror in inst["ror_ids"]:
                if ror in wanted:
                    entries_by_ror.setdefault(ror, []).append(inst)
        for ror, entries in entries_by_ror.items():
            record = out.setdefault(ror, {"ror_id": ror, "repositories": []})
            record["repositories"].append(project_repository(repo, ror, entries))
    for record in out.values():
        record["repositories"].sort(key=lambda r: r["re3data_id"])
    return out


def project_repository(repo: dict, ror: str, entries: list[dict]) -> dict:
    """Per-repository projection. Dates are source strings, not parsed: the
    export mixes ``YYYY``, ISO dates and free text."""
    return {
        "re3data_id": repo["id"],
        "re3data_url": REPOSITORY_URL.format(repo["id"]),
        "doi": repo["doi"],
        "name": repo["name"],
        "name_language": repo["name_language"],
        "url": repo["url"],
        "types": repo["types"],
        "start_date": repo["start_date"],
        "closed_date": repo["closed_date"],
        "offline_date": repo["offline_date"],
        "entry_date": repo["entry_date"],
        "last_update": repo["last_update"],
        "source": f"sources/{repo['id']}.xml",
        "matched_institutions": [
            {
                "position": inst["position"],
                "ror_id": ror,
                "name": inst["name"],
                "name_language": inst["name_language"],
                "responsibility_types": inst["responsibility_types"],
                "responsibility_start_date": inst["responsibility_start_date"],
                "responsibility_end_date": inst["responsibility_end_date"],
            }
            for inst in sorted(entries, key=lambda i: i["position"])
        ],
    }


def identifier_report(repos: dict[str, dict]) -> tuple[int, list[dict]]:
    """Count accepted non-canonical spellings; list rejected ROR-typed values."""
    normalized = 0
    rejected = []
    for rid in sorted(repos):
        for item in repos[rid]["ror_values"]:
            if item["canonical"] is None:
                rejected.append({"re3data_id": rid, "value": item["value"]})
            elif item["value"] != item["canonical"]:
                normalized += 1
    return normalized, rejected


# --- Writing -----------------------------------------------------------------


def suffix_of(ror: str) -> str:
    return ror.rstrip("/").rsplit("/", 1)[-1]


def write_layer(target: Path, projection: dict[str, dict], raw: dict[str, bytes]) -> None:
    """Write records/, records.json and sources/ into an empty ``target``."""
    records_dir = target / "records"
    sources_dir = target / "sources"
    records_dir.mkdir(parents=True)
    sources_dir.mkdir(parents=True)

    combined = [projection[ror] for ror in sorted(projection, key=suffix_of)]
    for record in combined:
        R.dump_json(record, records_dir / f"{suffix_of(record['ror_id'])}.json")
    R.dump_json(combined, target / "records.json")

    for rid in sorted({r["re3data_id"] for rec in combined for r in rec["repositories"]}):
        (sources_dir / f"{rid}.xml").write_bytes(raw[rid])


def build_meta(
    projection: dict[str, dict],
    ror_ids: list[str],
    stats: dict,
    normalized: int,
    rejected: list[dict],
    *,
    retrieved: str,
    method: str,
    last_modified: str | None,
) -> dict:
    matched = sorted(projection, key=suffix_of)
    missing = [rid for rid in ror_ids if rid not in projection]
    repo_ids = {r["re3data_id"] for rec in projection.values() for r in rec["repositories"]}
    relationships = sum(len(rec["repositories"]) for rec in projection.values())
    entries = sum(
        len(r["matched_institutions"]) for rec in projection.values() for r in rec["repositories"]
    )
    last_updates = [
        r["last_update"] for rec in projection.values() for r in rec["repositories"] if r["last_update"]
    ]
    return {
        "source": "https://www.re3data.org/",
        "export": EXPORT_URL,
        "retrieval_method": (
            "download of the full daily XML export, filtered locally"
            if method == "download"
            else "local copy of the full daily XML export (--input), filtered locally"
        ),
        "retrieved": retrieved,
        "export_last_modified": last_modified,
        "export_repositories": stats["repositories"],
        "export_ror_identifiers": stats["ror_identifiers"],
        "schema_namespace": SCHEMA_NAMESPACE,
        "schema_version": SCHEMA_VERSION,
        "schema_documentation": SCHEMA_DOC,
        "projection_version": PROJECTION_VERSION,
        "license": "CC0-1.0",
        "license_note": (
            "re3data waives all rights to its database entries (CC0 1.0). This "
            "does not cover the re3data website (CC BY 4.0) or the research data "
            "held in the listed repositories, which carry their own licences."
        ),
        "access": "open; no authentication; daily export at the URL above",
        "ror_total": len(ror_ids),
        "matched": len(matched),
        "missing": len(missing),
        "repositories": len(repo_ids),
        "relationships": relationships,
        "matched_institution_entries": entries,
        "latest_matched_last_update": max(last_updates) if last_updates else None,
        "normalized_ror_identifiers": normalized,
        "rejected_ror_identifiers": rejected,
        "counters_note": (
            "matched/missing count ROR records. repositories counts distinct "
            "re3data ids among all matches; relationships counts ROR record - "
            "repository pairs, so a repository naming two Saxon organizations "
            "adds one repository but two relationships. "
            "matched_institution_entries counts the institution entries behind "
            "those pairs (one pair can rest on several entries). "
            "normalized/rejected_ror_identifiers cover every ROR-typed "
            "identifier in the export, Saxon or not."
        ),
        "note": (
            "Derived companion data: re3data repositories whose institution "
            "entries name a Saxon ROR record by explicit ROR identifier. A "
            "missing ROR id means no such link was found in the evaluated "
            "export, not that the organization has no repository. Roles are "
            "re3data responsibility types, not statements about operation, "
            "ownership or eligibility."
        ),
        "matched_ror_ids": matched,
        "missing_ror_ids": missing,
    }


# --- Consistency -------------------------------------------------------------


def _source_problems(
    layer: Path, sources: set[str], combined: list, ror_ids: list[str]
) -> list[str]:
    """Rebuild the projection from the stored source XML and report differences."""
    problems: list[str] = []
    parsed: dict[str, dict] = {}
    for rid in sorted(sources):
        path = layer / "sources" / f"{rid}.xml"
        try:
            repo = parse_repository(path.read_bytes(), f"sources/{rid}.xml")
        except (ExportError, OSError) as exc:
            problems.append(f"re3data source unusable: {exc}")
            continue
        if repo["id"] != rid:
            problems.append(f"re3data sources/{rid}.xml holds repository {repo['id']}")
            continue
        parsed[rid] = repo

    rebuilt = match(parsed, ror_ids)
    stored = {rec.get("ror_id"): rec for rec in combined if isinstance(rec, dict)}
    for ror in sorted(set(stored) | set(rebuilt), key=lambda x: suffix_of(x or "")):
        old = {r.get("re3data_id"): r for r in (stored.get(ror) or {}).get("repositories") or []}
        new = {r["re3data_id"]: r for r in (rebuilt.get(ror) or {}).get("repositories") or []}
        for rid in sorted(set(old) | set(new), key=str):
            if rid not in parsed:
                continue  # already reported as missing or unusable above
            if rid not in new:
                problems.append(f"re3data {rid}: source does not link {ror}")
            elif rid not in old:
                problems.append(f"re3data {rid}: source links {ror}, projection does not")
            elif old[rid] != new[rid]:
                fields = sorted(k for k in set(old[rid]) | set(new[rid])
                                if old[rid].get(k) != new[rid].get(k))
                problems.append(
                    f"re3data {rid} for {ror}: projection differs from source in {', '.join(fields)}"
                )
    return problems


def check_layer(layer: Path, block: dict, ror_ids: list[str]) -> list[str]:
    """Consistency problems between the layer on disk and its meta block."""
    problems: list[str] = []
    try:
        combined = json.loads((layer / "records.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return [f"re3data records.json unreadable: {exc}"]

    known = set(ror_ids)
    by_suffix = {}
    for rec in combined:
        ror = rec.get("ror_id", "")
        if ror not in known:
            problems.append(f"re3data record {ror!r} is not in data/records.json")
        by_suffix[suffix_of(ror)] = rec
    if [r.get("ror_id") for r in combined] != sorted(
        (r.get("ror_id") for r in combined), key=lambda x: suffix_of(x or "")
    ):
        problems.append("re3data records.json is not sorted by ROR id suffix")

    files = {p.stem: p for p in (layer / "records").glob("*.json")}
    for sfx in sorted(set(by_suffix) - set(files)):
        problems.append(f"re3data records.json entry {sfx} has no per-record file")
    for sfx in sorted(set(files) - set(by_suffix)):
        problems.append(f"re3data records/{sfx}.json has no records.json entry")
    for sfx in sorted(set(files) & set(by_suffix)):
        if json.loads(files[sfx].read_text(encoding="utf-8")) != by_suffix[sfx]:
            problems.append(f"re3data records/{sfx}.json differs from records.json")

    referenced: set[str] = set()
    pairs = entries = 0
    for rec in combined:
        repos = rec.get("repositories") or []
        if not repos:
            problems.append(f"re3data record {rec.get('ror_id')} has no repositories")
        ids = [r.get("re3data_id") for r in repos]
        if len(ids) != len(set(ids)):
            problems.append(f"re3data record {rec.get('ror_id')} repeats a repository")
        for repo in repos:
            pairs += 1
            referenced.add(repo.get("re3data_id"))
            if repo.get("source") != f"sources/{repo.get('re3data_id')}.xml":
                problems.append(f"re3data {repo.get('re3data_id')}: unexpected source path")
            insts = repo.get("matched_institutions") or []
            if not insts:
                problems.append(f"re3data {repo.get('re3data_id')}: no matched institution entry")
            for inst in insts:
                entries += 1
                if inst.get("ror_id") != rec.get("ror_id"):
                    problems.append(
                        f"re3data {repo.get('re3data_id')}: institution entry "
                        f"{inst.get('position')} belongs to {inst.get('ror_id')}"
                    )

    sources = {p.stem for p in (layer / "sources").glob("*.xml")}
    for rid in sorted(referenced - sources):
        problems.append(f"re3data source sources/{rid}.xml is missing")
    for rid in sorted(sources - referenced):
        problems.append(f"re3data source sources/{rid}.xml is not referenced")
    problems += _source_problems(layer, sources, combined, ror_ids)

    expected = {
        "ror_total": len(ror_ids),
        "matched": len(combined),
        "missing": len(ror_ids) - len(combined),
        "repositories": len(referenced),
        "relationships": pairs,
        "matched_institution_entries": entries,
        "matched_ror_ids": [r.get("ror_id") for r in combined],
        "missing_ror_ids": [rid for rid in ror_ids if rid not in {r.get("ror_id") for r in combined}],
    }
    for key, value in expected.items():
        if block.get(key) != value:
            problems.append(f"meta reuse.re3data.{key} is {block.get(key)!r}, expected {value!r}")
    return problems


# --- Publish -----------------------------------------------------------------


def load_ror_ids(data_dir: Path) -> list[str]:
    with open(data_dir / "records.json", encoding="utf-8") as fh:
        return [R.ror_id(r) for r in json.load(fh)]


def update(
    zip_path: Path,
    data_dir: Path,
    *,
    retrieved: str,
    method: str = "download",
    last_modified: str | None = None,
    allow_empty: bool = False,
) -> dict:
    """Parse, match, validate, then publish; returns the new meta block."""
    ror_ids = load_ror_ids(data_dir)
    repos, raw, stats = read_export(zip_path)
    projection = match(repos, ror_ids)
    normalized, rejected = identifier_report(repos)
    block = build_meta(
        projection, ror_ids, stats, normalized, rejected,
        retrieved=retrieved, method=method, last_modified=last_modified,
    )

    meta = R.read_meta(data_dir)
    previous = (meta.get("reuse") or {}).get(SOURCE_NAME) or {}
    if not projection and previous.get("matched", 0) > 0 and not allow_empty:
        raise ExportError(
            f"no Saxon ROR record matched, but the previous run had "
            f"{previous['matched']} -- refusing to empty the layer "
            "(use --allow-empty if this is real)"
        )

    reuse_dir = data_dir / "reuse"
    reuse_dir.mkdir(parents=True, exist_ok=True)
    live = reuse_dir / SOURCE_NAME
    staging = Path(tempfile.mkdtemp(dir=reuse_dir, prefix=f".{SOURCE_NAME}-staging-"))
    retired = None
    # The staging directory may end up holding the only copy of the old layer.
    keep_staging = False
    try:
        write_layer(staging / "layer", projection, raw)
        problems = check_layer(staging / "layer", block, ror_ids)
        if problems:
            raise ExportError("staged layer is inconsistent:\n  - " + "\n  - ".join(problems))

        meta.setdefault("reuse", {})[SOURCE_NAME] = block
        if live.exists():
            retired = staging / "previous"
            os.replace(live, retired)
        swapped_in = False
        try:
            os.replace(staging / "layer", live)
            swapped_in = True
            R.dump_json_atomic(meta, data_dir / "meta.json")
        except BaseException:
            # meta.json is still the old one; restore the matching layer.
            try:
                if swapped_in:
                    os.replace(live, staging / "rejected")
                if retired is not None:
                    os.replace(retired, live)
            except BaseException as restore_exc:
                keep_staging = True
                print(
                    f"ERROR: could not restore the previous re3data layer "
                    f"({restore_exc}); it is preserved at {retired}",
                    file=sys.stderr,
                )
            raise
    finally:
        if not keep_staging:
            shutil.rmtree(staging, ignore_errors=True)
    return block


# --- Download ----------------------------------------------------------------


def download_export(dest: Path, retries: int = 3, backoff: float = 5.0) -> str | None:
    """Download the export to ``dest``; return its HTTP Last-Modified (ISO).

    A short read fails the attempt; truncation is also caught by the ZIP
    reader (central directory at the end, CRC per member).
    """
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(EXPORT_URL, headers={"User-Agent": R.USER_AGENT})
            with urllib.request.urlopen(req, timeout=300) as resp, open(dest, "wb") as out:
                expected = resp.headers.get("Content-Length")
                size = 0
                while chunk := resp.read(1 << 20):
                    out.write(chunk)
                    size += len(chunk)
                if expected is not None and size != int(expected):
                    raise ExportError(f"incomplete download: {size} of {expected} bytes")
                lm = resp.headers.get("Last-Modified")
            if lm:
                try:
                    return parsedate_to_datetime(lm).isoformat()
                except (TypeError, ValueError):
                    return None
            return None
        except Exception as exc:  # noqa: BLE001 - retry on any transient error
            last = exc
            if attempt < retries - 1:
                time.sleep(backoff * (attempt + 1))
    raise ExportError(f"download of {EXPORT_URL} failed after {retries} attempts: {last}")


# --- CLI ---------------------------------------------------------------------


def own_pairing_problems(data_dir: Path) -> list[str]:
    """ROR pairing plus this layer's only: after a ROR removal, another
    companion's orphan awaits that companion's own refresh."""
    return R.validate_pairing(data_dir, reuse_sources=[SOURCE_NAME])


def run_check(data_dir: Path) -> int:
    meta = R.read_meta(data_dir)
    block = (meta.get("reuse") or {}).get(SOURCE_NAME)
    if block is None:
        print("No reuse.re3data block in meta.json -- nothing to check.")
        return 0
    problems = check_layer(data_dir / "reuse" / SOURCE_NAME, block, load_ror_ids(data_dir))
    problems += own_pairing_problems(data_dir)
    for p in problems:
        print("  -", p, file=sys.stderr)
    if problems:
        print("ERROR: re3data companion is inconsistent", file=sys.stderr)
        return 1
    print("re3data companion is consistent.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--input", type=Path, help="Use this export ZIP instead of downloading")
    parser.add_argument(
        "--retrieved",
        help="Retrieval date (YYYY-MM-DD) to record with --input; defaults to today (UTC)",
    )
    parser.add_argument(
        "--allow-empty",
        action="store_true",
        help="Accept a result with no matches even though the previous run had some",
    )
    parser.add_argument(
        "--check", action="store_true", help="Only validate the committed layer and exit"
    )
    args = parser.parse_args(argv)
    data_dir = R.DATA_DIR

    if args.check:
        return run_check(data_dir)
    if not (data_dir / "records.json").exists():
        print("ERROR: data/records.json not found. Run update_ror.py first.", file=sys.stderr)
        return 1
    if args.retrieved and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", args.retrieved):
        parser.error("--retrieved must be YYYY-MM-DD")

    try:
        if args.input:
            block = update(
                args.input, data_dir,
                retrieved=args.retrieved or R.today_iso(),
                method="local-file", allow_empty=args.allow_empty,
            )
        else:
            with tempfile.TemporaryDirectory(prefix="re3data-export-") as tmp:
                zip_path = Path(tmp) / "re3data-repositories.zip"
                print(f"Downloading {EXPORT_URL}")
                last_modified = download_export(zip_path)
                block = update(
                    zip_path, data_dir,
                    retrieved=R.today_iso(), method="download",
                    last_modified=last_modified, allow_empty=args.allow_empty,
                )
    except ExportError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        print("The previous re3data companion was left unchanged.", file=sys.stderr)
        return 1

    problems = own_pairing_problems(data_dir)
    if problems:
        print("ERROR: data consistency problems:", file=sys.stderr)
        for p in problems:
            print("  -", p, file=sys.stderr)
        return 1

    print(
        f"Evaluated {block['export_repositories']} re3data repositories: "
        f"{block['matched']} / {block['ror_total']} ROR records matched "
        f"({block['missing']} without explicit link), {block['repositories']} "
        f"repositories, {block['relationships']} relationships, "
        f"{len(block['rejected_ror_identifiers'])} rejected ROR identifiers."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
