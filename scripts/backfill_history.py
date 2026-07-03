#!/usr/bin/env python3
"""One-time historical backfill of the Saxon ROR subset.

Walks **every** published version of the ROR Zenodo concept in chronological
order (dumps go back to 2022) and, for each dump, filters it to the Saxon subset
and creates one git commit whose author/committer date is the dump's Zenodo
publication date. The result: ``git log data/records/`` reads as a true timeline
of Saxon ROR records, with a schema switch from v1 to v2 partway through.

Schema handling is automatic (see ``ror_lib.load_records_from_zip``): early
dumps are filtered via the v1 GeoNames admin1 id, later dumps via the v2
subdivision fields. Records are stored in whatever schema the dump provided --
v1 records are **not** converted to v2, so the large diff at the schema
transition is honest and expected.

This script is **not** part of the scheduled workflow. Run it once, manually, on
a dedicated branch, then merge that branch via PR before the first scheduled
update. The backfill covers ROR only (OpenAlex has no public snapshot history).

Usage:
    git switch -c backfill
    python scripts/backfill_history.py            # all versions
    python scripts/backfill_history.py --limit 3  # smoke test (oldest 3)
    python scripts/backfill_history.py --dry-run  # no commits, just report
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import ror_lib as R


def git(*args: str, env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=R.REPO_ROOT,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )


def commit_version(version: dict, count: int, schema: str, dry_run: bool) -> None:
    # Date the commit to the dump's real Zenodo creation moment (a full ISO
    # datetime, e.g. 2026-06-23T17:56:38+00:00), not a placeholder time of day.
    # Both author and committer dates are set: these are local commits, so no
    # later rebase/merge rewrites them.
    when = version["created"] or f"{version['publication_date']}T12:00:00"
    message = (
        f"data: ROR dump {version['version']} ({version['publication_date']})\n\n"
        f"Saxon subset: {count} records ({schema} schema).\n"
        f"Source: ROR data dump {version['version']}, "
        f"DOI {version['doi']} (concept {R.ZENODO_CONCEPT_DOI}).\n"
        f"Filtered from https://zenodo.org/records/{version['id']}."
    )
    if dry_run:
        print(f"    [dry-run] would commit as of {when}")
        return

    git("add", "data")
    env = dict(os.environ)
    env["GIT_AUTHOR_DATE"] = when
    env["GIT_COMMITTER_DATE"] = when
    git("commit", "-m", message, env=env)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, help="Only process the oldest N versions")
    parser.add_argument(
        "--dry-run", action="store_true", help="Do not write files or commit"
    )
    args = parser.parse_args()

    print("Fetching all ROR dump versions from Zenodo ...")
    versions = R.zenodo_all_versions()
    if args.limit:
        versions = versions[: args.limit]
    print(f"{len(versions)} versions to process (oldest first)")

    for i, version in enumerate(versions, 1):
        print(
            f"[{i}/{len(versions)}] {version['version']} "
            f"({version['publication_date']}) ..."
        )
        with tempfile.TemporaryDirectory(prefix="saxon-ror-bf-") as tmp:
            zip_path = Path(tmp) / version["file_name"]
            R.download(version["download_url"], zip_path)
            subset, schema = R.load_saxon_subset(zip_path)

        print(f"    {len(subset)} Saxon records ({schema} schema)")
        if not subset:
            print(
                "    WARNING: empty subset for this dump -- committing anyway, "
                "but check the filter.",
                file=sys.stderr,
            )

        if args.dry_run:
            continue

        R.write_subset(subset, schema)
        meta = {
            "ror": {
                "dump_version": version["version"],
                "zenodo_doi": version["doi"],
                "zenodo_concept_doi": R.ZENODO_CONCEPT_DOI,
                "schema_version": schema,
                "retrieved": version["publication_date"],
                "publication_date": version["publication_date"],
                "zenodo_created": version["created"],
                "record_count": len(subset),
                "license": "CC0-1.0",
                "source": "https://ror.org/",
                "dump_source": f"https://doi.org/{R.ZENODO_CONCEPT_DOI}",
                "note": "Historical backfill commit; data as of the dump above.",
            }
        }
        R.write_meta(meta)

        problems = R.validate_pairing()
        if problems:
            print("    ERROR: consistency problems:", file=sys.stderr)
            for p in problems:
                print("      -", p, file=sys.stderr)
            return 1

        commit_version(version, len(subset), schema, args.dry_run)

    print("Backfill complete.")
    if not args.dry_run:
        print("Review with:  git log --format='%ad %s' --date=short data/records/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
