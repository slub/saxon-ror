#!/usr/bin/env python3
"""Fetch OpenAlex institution entities for the Saxon ROR subset.

OpenAlex is a *companion* (derived) data source: ROR remains authoritative.
OpenAlex institutions are keyed by ROR id, so this script reads the ROR subset
(``data/records.json``), batches the ids into ``filter=ror:<id1>|<id2>|...``
queries against the OpenAlex institutions endpoint (polite pool via ``mailto``),
and stores each institution entity unmodified as:

    data/reuse/openalex/records/<ror-id-suffix>.json
    data/reuse/openalex/records.json      the combined array

Match statistics land in the ``reuse.openalex`` block of ``data/meta.json``.
ROR records without an OpenAlex counterpart are expected and normal -- they are
counted, not treated as errors. Only institution entities are fetched; no
works/publication metadata is retrieved (the entities' built-in aggregates such
as ``works_count`` and ``counts_by_year`` are part of the entity and kept).

Usage:
    python scripts/update_openalex.py [--mailto you@example.org]
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.parse
from pathlib import Path

import ror_lib as R

OPENALEX_DIR = R.REUSE_DIR / "openalex"
BATCH_SIZE = 50  # OpenAlex allows up to 50 OR values per filter
DEFAULT_MAILTO = "bibliometrie@slub-dresden.de"


def fetch_batch(ror_ids: list[str], mailto: str) -> list[dict]:
    """Fetch all institutions matching any of ``ror_ids`` (cursor-paged)."""
    results: list[dict] = []
    filter_value = "|".join(ror_ids)
    cursor = "*"
    while cursor:
        params = urllib.parse.urlencode(
            {
                "filter": f"ror:{filter_value}",
                "per-page": 200,
                "cursor": cursor,
                "mailto": mailto,
            }
        )
        page = R.http_json(f"https://api.openalex.org/institutions?{params}")
        results.extend(page.get("results", []))
        cursor = page.get("meta", {}).get("next_cursor")
        time.sleep(0.2)
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mailto",
        default=DEFAULT_MAILTO,
        help="Contact e-mail for the OpenAlex polite pool",
    )
    args = parser.parse_args()

    records_json = R.DATA_DIR / "records.json"
    if not records_json.exists():
        print(
            "ERROR: data/records.json not found. Run update_ror.py first.",
            file=sys.stderr,
        )
        return 1

    with open(records_json, encoding="utf-8") as fh:
        ror_records = json.load(fh)
    ror_ids = [R.ror_id(r) for r in ror_records]
    suffix_by_id = {R.ror_id(r): R.ror_suffix(r) for r in ror_records}
    print(f"{len(ror_ids)} ROR records to look up in OpenAlex")

    # --- Fetch in batches ----------------------------------------------------
    institutions_by_ror: dict[str, dict] = {}
    for start in range(0, len(ror_ids), BATCH_SIZE):
        batch = ror_ids[start : start + BATCH_SIZE]
        print(f"  batch {start // BATCH_SIZE + 1}: {len(batch)} ids")
        for inst in fetch_batch(batch, args.mailto):
            ror = inst.get("ror")
            if ror in suffix_by_id:
                institutions_by_ror[ror] = inst

    matched = sorted(institutions_by_ror)
    missing = [rid for rid in ror_ids if rid not in institutions_by_ror]
    print(f"Matched {len(matched)} / {len(ror_ids)} (misses: {len(missing)})")

    # --- Write (rebuild records dir to drop stale entries) -------------------
    records_dir = OPENALEX_DIR / "records"
    if records_dir.exists():
        for stale in records_dir.glob("*.json"):
            stale.unlink()
    records_dir.mkdir(parents=True, exist_ok=True)

    combined = []
    for ror in matched:
        inst = institutions_by_ror[ror]
        R.dump_json(inst, records_dir / f"{suffix_by_id[ror]}.json")
        combined.append(inst)
    R.dump_json(combined, OPENALEX_DIR / "records.json")

    # --- meta.json -----------------------------------------------------------
    meta = R.read_meta()
    meta.setdefault("reuse", {})
    meta["reuse"]["openalex"] = {
        "source": "https://openalex.org/",
        "api": "https://api.openalex.org/institutions",
        "retrieved": R.today_iso(),
        "matched": len(matched),
        "ror_total": len(ror_ids),
        "missing": len(missing),
        "missing_ror_ids": missing,
        "license": "CC0-1.0",
        "access": "open (no authentication required)",
        "note": (
            "Derived companion data keyed by ROR id. Institution entities only; "
            "no works/publication metadata fetched. May lag behind or diverge "
            "from ROR."
        ),
    }
    R.write_meta(meta)

    problems = R.validate_pairing()
    if problems:
        print("ERROR: data consistency problems:", file=sys.stderr)
        for p in problems:
            print("  -", p, file=sys.stderr)
        return 1

    print(f"Wrote {len(combined)} OpenAlex entities to {OPENALEX_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
