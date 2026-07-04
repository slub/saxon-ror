#!/usr/bin/env python3
"""Link Saxon ROR records to their ROR curation requests.

Records are verbatim ROR data and are corrected upstream via ROR's curation
process, tracked as issues in ``ror-community/ror-updates``. This script keeps a
small **curated map** of record -> curation issue numbers and, at deploy time,
enriches it with each issue's live title/state for the website.

Two modes:

``--seed``
    One-time (occasional) bootstrap. Searches ``ror-community/ror-updates`` for
    each Saxon record's ROR URL and merges the found issue numbers into
    ``data/curation.json``:

        { "<suffix>": [11728, ...] }

    Two kinds of curation issues are captured from a single search per record:

    * **update requests** put the target record's ROR URL in their *title*, so
      a title match is trusted directly (precise by construction).
    * **add requests** cannot carry the ROR URL in the title (the record does
      not exist yet when the request is filed). Instead ``ror-curator-bot``
      announces the release in a *comment*:

          Assigned ROR ID https://ror.org/<suffix> in release v2.8.

      So any hit whose URL is *not* in the title is treated as an add-candidate
      and only kept if a comment contains ``Assigned ROR ID https://ror.org/<suffix>``.
      This bot convention is recent; pre-bot add requests used ad-hoc phrasings
      and are not auto-discovered (hand-add them to ``data/curation.json``).

    ``data/curation.json`` is committed and hand-maintainable afterwards.

default (enrich)
    Reads ``data/curation.json`` and fetches each referenced issue's current
    title/state/url, writing the site-facing overlay (keyed by ROR suffix):

        { "<suffix>": [ {"number":11728, "title":"...", "state":"closed",
                         "url":"https://github.com/..."} ] }

    The enriched output is generated at deploy time and **not** committed;
    ``pages.yml`` writes it to ``_deploy/data/issues.json``.

Auth is optional but recommended (``GITHUB_TOKEN``) for higher rate limits.
Note the GitHub **search** API is limited to ~30 requests/minute, so ``--seed``
throttles and takes a few minutes across the full record set.

Usage:
    python scripts/update_curation.py --seed          # bootstrap data/curation.json
    python scripts/update_curation.py --out _deploy/data/issues.json  # enrich
"""

from __future__ import annotations

import argparse
import json
import os
import time
import urllib.parse
import urllib.request
from pathlib import Path

CURATION_REPO = "ror-community/ror-updates"
API = "https://api.github.com"
REPO_ROOT = Path(__file__).resolve().parent.parent
RECORDS_JSON = REPO_ROOT / "data" / "records.json"
CURATION_JSON = REPO_ROOT / "data" / "curation.json"


def _request(url: str) -> urllib.request.Request:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "saxon-ror/1.0 (+https://github.com/slub/saxon-ror)",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return urllib.request.Request(url, headers=headers)


def _get(url: str, retries: int = 4, backoff: float = 3.0):
    last = None
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(_request(url), timeout=60) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as exc:
            last = exc
            # 403 with rate-limit: wait longer before retrying.
            wait = backoff * (attempt + 1) * (4 if exc.code == 403 else 1)
            if attempt < retries - 1:
                time.sleep(wait)
        except Exception as exc:  # noqa: BLE001
            last = exc
            if attempt < retries - 1:
                time.sleep(backoff * (attempt + 1))
    raise RuntimeError(f"GET {url} failed: {last}")


def _suffixes() -> list[str]:
    records = json.loads(RECORDS_JSON.read_text(encoding="utf-8"))
    return [r.get("id", "").rstrip("/").rsplit("/", 1)[-1] for r in records if r.get("id")]


def _comment_bodies(number: int, cache: dict[int, list[str]]) -> list[str]:
    """Return issue ``number``'s comment bodies, fetched once and cached by number."""
    if number not in cache:
        try:
            comments = _get(f"{API}/repos/{CURATION_REPO}/issues/{number}/comments?per_page=100")
            cache[number] = [c.get("body") or "" for c in comments]
        except RuntimeError as exc:
            print(f"  war: comments for #{number} not fetched: {exc}")
            cache[number] = []
    return cache[number]


def _has_add_release_comment(number: int, sfx: str, cache: dict[int, list[str]]) -> bool:
    """True if issue ``number`` carries the curator-bot release comment for ``sfx``.

    Add requests announce the assigned ROR ID in a comment like
    ``Assigned ROR ID https://ror.org/<sfx> in release v2.8.``; this is the only
    reliable, machine-readable signal that the issue produced *this* record.

    The needle is suffix-specific, so the check runs per ``sfx`` — only the fetched
    comment bodies are cached (by issue number), never the boolean verdict.
    """
    needle = f"Assigned ROR ID https://ror.org/{sfx}"
    return any(needle in body for body in _comment_bodies(number, cache))


def seed(existing: dict[str, list[int]]) -> dict[str, list[int]]:
    """Search curation issues by ROR URL; merge update + add requests into ``existing``."""
    merged = {k: list(v) for k, v in existing.items()}
    suffixes = _suffixes()
    comment_cache: dict[int, list[str]] = {}
    for i, sfx in enumerate(suffixes, 1):
        known = set(merged.get(sfx, []))
        # Unqualified search returns title, body and comment matches in one call.
        q = f"repo:{CURATION_REPO} ror.org/{sfx}"
        url = f"{API}/search/issues?q={urllib.parse.quote(q)}&per_page=50"
        data = _get(url)
        nums: set[int] = set()
        for it in data.get("items", []):
            n = it["number"]
            if n in known:
                continue  # already recorded — no need to re-classify/re-fetch
            if f"ror.org/{sfx}" in (it.get("title") or ""):
                nums.add(n)  # URL in title -> trusted update request
            elif _has_add_release_comment(n, sfx, comment_cache):
                nums.add(n)  # URL only in comments + release announcement -> add request
        if nums:
            merged[sfx] = sorted(known | nums, reverse=True)
            print(f"[{i}/{len(suffixes)}] {sfx}: {sorted(nums, reverse=True)}")
        # Search API allows ~30 requests/minute.
        time.sleep(2.2)
    return dict(sorted(merged.items()))


def enrich(curation: dict[str, list[int]]) -> dict[str, list[dict]]:
    """Fetch each referenced issue's live title/state/url."""
    cache: dict[int, dict | None] = {}
    out: dict[str, list[dict]] = {}
    for sfx, numbers in curation.items():
        entries = []
        for n in numbers:
            if n not in cache:
                try:
                    issue = _get(f"{API}/repos/{CURATION_REPO}/issues/{n}")
                    cache[n] = {
                        "number": issue["number"],
                        "title": issue["title"],
                        "state": issue["state"],
                        "url": issue["html_url"],
                    }
                except RuntimeError as exc:
                    print(f"  war: issue #{n} for {sfx} not fetched: {exc}")
                    cache[n] = None
            if cache[n]:
                entries.append(cache[n])
        if entries:
            entries.sort(key=lambda e: e["number"], reverse=True)
            out[sfx] = entries
    return dict(sorted(out.items()))


def _write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2, sort_keys=False)
        fh.write("\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", action="store_true", help="Bootstrap data/curation.json via title search")
    parser.add_argument("--out", type=Path, default=Path("data/issues.json"), help="Enriched overlay path")
    args = parser.parse_args()

    existing = json.loads(CURATION_JSON.read_text(encoding="utf-8")) if CURATION_JSON.exists() else {}

    if args.seed:
        print(f"Seeding from {CURATION_REPO} (title search) ...")
        mapping = seed(existing)
        _write_json(CURATION_JSON, mapping)
        print(f"Wrote {CURATION_JSON} ({len(mapping)} records with curation issues).")
        return 0

    print(f"Enriching {len(existing)} records from {CURATION_REPO} ...")
    enriched = enrich(existing)
    _write_json(args.out, enriched)
    total = sum(len(v) for v in enriched.values())
    print(f"Wrote {args.out} ({len(enriched)} records, {total} issues).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
