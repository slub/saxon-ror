#!/usr/bin/env python3
"""Update the authoritative Saxon ROR subset from the latest Zenodo dump.

Downloads the newest ROR data dump (resolved from the concept DOI), filters it
to organizations located in Saxony, and writes:

    data/records/<ror-id-suffix>.json   one file per record, stored unmodified
    data/records.json                   the combined array
    data/records.csv                    convenience CSV with core fields
    data/meta.json                      the ``ror`` provenance block

The raw dump is downloaded to a temporary directory outside the repository and
never committed. The script fails loudly if the filtered set is empty or shrinks
by more than 20 % versus the previous run -- a guard against upstream schema
changes silently breaking the filter.

Usage:
    python scripts/update_ror.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import ror_lib as R

SHRINK_GUARD = 0.20  # fail if the subset shrinks by more than this fraction


def main() -> int:
    print("Resolving latest ROR dump from Zenodo concept", R.ZENODO_CONCEPT_DOI)
    version = R.zenodo_latest()
    print(
        f"Latest: {version['version']} "
        f"({version['publication_date']}, DOI {version['doi']})"
    )

    with tempfile.TemporaryDirectory(prefix="saxon-ror-") as tmp:
        zip_path = Path(tmp) / version["file_name"]
        print("Downloading", version["download_url"])
        R.download(version["download_url"], zip_path)

        print("Loading and filtering records ...")
        subset, schema = R.load_saxon_subset(zip_path)
        print(f"Saxon subset: {len(subset)} records ({schema} schema)")

    # --- Quality guards ------------------------------------------------------
    if not subset:
        print("ERROR: Saxon subset is empty -- filter likely broken.", file=sys.stderr)
        return 1

    previous = R.read_meta().get("ror", {}).get("record_count")
    if previous:
        shrink = (previous - len(subset)) / previous
        if shrink > SHRINK_GUARD:
            print(
                f"ERROR: subset shrank by {shrink:.0%} "
                f"({previous} -> {len(subset)}), exceeding the "
                f"{SHRINK_GUARD:.0%} guard. Aborting.",
                file=sys.stderr,
            )
            return 1

    # --- Write ---------------------------------------------------------------
    R.write_subset(subset, schema)

    meta = R.read_meta()
    meta["ror"] = {
        "dump_version": version["version"],
        "zenodo_doi": version["doi"],
        "zenodo_concept_doi": R.ZENODO_CONCEPT_DOI,
        "schema_version": schema,
        "retrieved": R.today_iso(),
        "publication_date": version["publication_date"],
        "zenodo_created": version["created"],
        "record_count": len(subset),
        "license": "CC0-1.0",
        "source": "https://ror.org/",
        "dump_source": f"https://doi.org/{R.ZENODO_CONCEPT_DOI}",
    }
    R.write_meta(meta)

    problems = R.validate_pairing()
    if problems:
        print("ERROR: data consistency problems:", file=sys.stderr)
        for p in problems:
            print("  -", p, file=sys.stderr)
        return 1

    print(f"Wrote {len(subset)} records to {R.DATA_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
