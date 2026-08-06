"""Read ROR's official per-release record deltas.

``ror-community/ror-records`` carries one directory per release holding every
record that release deployed -- new and updated together, in the schema of the
day. It is the deployment artifact itself, so it cannot be truncated the way a
release body can.

Read from the default branch rather than per-release tags: a tag only carries
the releases up to itself, so reconstructing the whole history from tags would
mean one download per release. The directories are append-only in practice, and
the guards do not take that on trust -- the release catalog may not lose a
release the overlay already records, and a delta only *enumerates* a section
once its file count is corroborated by the note's declared totals.

What it does *not* do is say whether a record was new. That has to be inferred,
by comparing the record against the last official state seen for it while
walking releases in order from the final pre-v1.0 dump. So a delta is not the
better source in general: a count-validated release-note section states "added"
or "updated" outright, and is used when there is one. The delta answers for the
sections a note never listed or listed short -- see ``update_history.py`` for
how the two are combined, cross-checked, and when a delta is trusted to
enumerate a section at all.

Standard library only, matching the rest of ``scripts/``.
"""

from __future__ import annotations

import json
import re
import tarfile
import zipfile
from pathlib import Path

import ror_lib as R

RECORDS_REPO = "ror-community/ror-records"
RECORDS_TARBALL = f"https://codeload.github.com/{RECORDS_REPO}/tar.gz/refs/heads/main"
DATA_REPO = "ror-community/ror-data"
DATA_TREE_API = f"https://api.github.com/repos/{DATA_REPO}/git/trees/main"
DATA_RAW = f"https://raw.githubusercontent.com/{DATA_REPO}/main"

# ROR released through September 2021 in step with GRID, and began independent
# curation and semantic versioning with v1.0 in March 2022. Presence in the last
# pre-v1.0 dump is therefore a statement about the GRID era, not a ROR release
# event -- so it gets its own pseudo-version rather than being folded into v1.0.
PRE_V1_VERSION = "pre-v1.0"

_DELTA_PATH_RE = re.compile(r"^[^/]+/(v[\d.]+)/(0[0-9a-z]{8})\.json$")
_DUMP_NAME_RE = re.compile(r"^(v[\d.]+)-(\d{4}-\d{2}-\d{2})-ror-data\.zip$")
_PRE_V1_NAME_RE = re.compile(r"^ror-(\d{4}-\d{2}-\d{2})\.zip$")


# --- Dump inventory ----------------------------------------------------------


def dump_inventory() -> tuple[dict[str, str], str, str]:
    """``({version: date}, pre_v1_file, pre_v1_date)`` from the ror-data listing.

    One request, and it dates the releases Zenodo does not carry -- v1.0.1 ships
    a dump and a delta but never reached the Zenodo concept.
    """
    tree = R.http_json(DATA_TREE_API).get("tree", [])
    names = [entry["path"] for entry in tree if entry.get("type") == "blob"]

    dates = {}
    for name in names:
        match = _DUMP_NAME_RE.match(name)
        if match:
            dates[match.group(1)] = match.group(2)

    pre = sorted(n for n in names if _PRE_V1_NAME_RE.match(n))
    if not pre:
        raise RuntimeError(f"{DATA_REPO} carries no pre-v1.0 ror-<date>.zip dump")
    return dates, pre[-1], _PRE_V1_NAME_RE.match(pre[-1]).group(1)


# --- Loading -----------------------------------------------------------------


def load_pre_v1(zip_path: Path, suffixes: set[str]) -> dict[str, dict]:
    """The wanted records as the last pre-v1.0 dump held them."""
    out: dict[str, dict] = {}
    with zipfile.ZipFile(zip_path) as zf:
        for member in zf.namelist():
            if not member.endswith(".json") or member.startswith("__MACOSX"):
                continue
            for record in json.loads(zf.read(member)):
                suffix = R.ror_suffix(record)
                if suffix in suffixes:
                    out[suffix] = record
    return out


def load_deltas(tar_path: Path, suffixes: set[str]) -> tuple[dict[str, dict[str, dict]], dict[str, int]]:
    """``({version: {suffix: record}}, {version: files})`` from the tarball.

    Only the wanted records are kept, but *every* file is counted: the total is
    what the release note's declared counts are checked against.
    """
    records: dict[str, dict[str, dict]] = {}
    totals: dict[str, int] = {}
    with tarfile.open(tar_path) as tf:
        for member in tf:
            match = _DELTA_PATH_RE.match(member.name)
            if not match:
                continue
            version, suffix = match.groups()
            totals[version] = totals.get(version, 0) + 1
            if suffix in suffixes:
                handle = tf.extractfile(member)
                if handle is not None:
                    records.setdefault(version, {})[suffix] = json.loads(handle.read())
    return records, totals


# --- Record state ------------------------------------------------------------

ACTIVE = "active"


def status(record: dict | None) -> str:
    """A record's ROR status, in either schema; both spell the field the same."""
    return (record or {}).get("status") or ""


def successors(record: dict | None) -> list[str]:
    """Successor ROR id suffixes, sorted.

    Reported as a plain detail, never interpreted. ROR uses a successor for
    organizational continuation, closure, restructuring *and* the correction of
    an erroneous or duplicate record, so the relationship alone does not say a
    merge happened.
    """
    out = set()
    for rel in (record or {}).get("relationships") or []:
        if (rel.get("type") or "").lower() == "successor" and rel.get("id"):
            out.add(rel["id"].rstrip("/").rsplit("/", 1)[-1])
    return sorted(out)
