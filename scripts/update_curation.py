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

    Each search hit is classified by where the ROR URL appears:

    * **update requests** usually put the target record's ROR URL in their
      *title*, so a title match is trusted directly (precise by construction).
    * **modify requests** whose title omits the URL (a newer template) still
      name the target in the body's structured ``ROR ID:`` field:

          ROR ID: https://ror.org/<suffix>

      A hit whose body ``ROR ID:`` field is this record is treated as an update.
      This is distinct from the ``Related organizations:`` URLs in the same body,
      so a record merely *referenced* by another request is not miscaptured.
    * **add requests** cannot carry the ROR URL in the title *or* the ROR ID
      field (the record does not exist yet when filed). Instead an *assignment
      comment* announces the release:

          Assigned ROR ID https://ror.org/<suffix> in release v2.8.

      So a remaining hit is treated as an add-candidate and only kept if a
      comment contains ``Assigned ROR ID https://ror.org/<suffix>``. Only the
      phrase is matched, never its author: the comment may be posted by either
      a human curator or ``ror-curator-bot``. The wording convention is recent;
      older add requests used ad-hoc phrasings and are not auto-discovered
      (hand-add them to ``data/curation.json``).

    ``data/curation.json`` is committed and hand-maintainable afterwards.

    ``--only <suffix> ...`` narrows the search to the named records. Every other
    record's links are carried over untouched, so the written map is complete
    either way.

default (enrich)
    Reads ``data/curation.json`` and fetches each referenced issue's current
    title/state/url, writing the site-facing overlay (keyed by ROR suffix):

        { "<suffix>": [ {"number":11728, "title":"...", "state":"closed",
                         "url":"https://github.com/..."} ] }

    The enriched output is generated at deploy time and **not** committed;
    ``pages.yml`` writes it to ``_deploy/data/issues.json``.

Auth is optional but recommended (``GITHUB_TOKEN``) for higher rate limits.
Note the GitHub **search** API is limited to ~30 requests/minute, so ``--seed``
throttles and takes a few minutes across the full record set. Search results are
also scoped to what the token can access, and an Actions ``GITHUB_TOKEN`` cannot
reach ``ror-community/ror-updates`` -- see ``_search_mode``, which probes for
this and falls back to unauthenticated search instead of finding nothing.

Usage:
    python scripts/update_curation.py --seed          # bootstrap data/curation.json
    python scripts/update_curation.py --seed --only 0202dx760 03s7gtk40  # just these
    python scripts/update_curation.py --out _deploy/data/issues.json  # enrich
"""

from __future__ import annotations

import argparse
import json
import os
import re
import time
import urllib.parse
import urllib.request
from pathlib import Path

CURATION_REPO = "ror-community/ror-updates"
# Saxon record whose ROR URL is certain to appear in the curation repo (TU
# Dresden, the subject of many requests). Probed by ``_search_mode`` only when
# data/curation.json holds no link yet, to tell "search cannot see the repo"
# apart from "this record genuinely has no curation request".
CANARY_SUFFIX = "042aqky30"
API = "https://api.github.com"
REPO_ROOT = Path(__file__).resolve().parent.parent
RECORDS_JSON = REPO_ROOT / "data" / "records.json"
CURATION_JSON = REPO_ROOT / "data" / "curation.json"


def _request(url: str, auth: bool = True) -> urllib.request.Request:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "saxon-ror/1.0 (+https://github.com/slub/saxon-ror)",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if auth and token:
        headers["Authorization"] = f"Bearer {token}"
    return urllib.request.Request(url, headers=headers)


def _get(url: str, retries: int = 4, backoff: float = 3.0, auth: bool = True):
    last = None
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(_request(url, auth=auth), timeout=60) as resp:
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


def _search(sfx: str, auth: bool) -> dict:
    """Run the issue search for one ROR suffix in the curation repo."""
    q = f"repo:{CURATION_REPO} ror.org/{sfx}"
    url = f"{API}/search/issues?q={urllib.parse.quote(q)}&per_page=50"
    return _get(url, auth=auth)


def _search_mode(existing: dict[str, list[int]]) -> tuple[bool, float]:
    """Decide whether searches may use the token, and how hard to throttle.

    The GitHub *search* API only returns resources the caller can access. A
    GitHub App installation token -- which is what ``GITHUB_TOKEN`` is inside
    Actions -- can access only the repository it was minted for, so searching
    ``ror-community/ror-updates`` with it yields **zero hits and no error**: the
    seed appears to run fine and quietly links nothing (observed in the weekly
    reseed workflow after v2.10).

    So probe with a record that must produce a hit -- one already linked in
    ``existing``, or ``CANARY_SUFFIX`` when the map is still empty, so that a
    from-scratch bootstrap is probed too. If the token search comes back empty,
    fall back to unauthenticated search (which is not installation-scoped) at
    its lower rate limit. If both come back empty, something else is broken --
    fail loudly rather than write an empty or unchanged map.

    Returns ``(auth, delay_seconds)``: ~30 requests/minute authenticated,
    ~10/minute unauthenticated. Unauthenticated limits are per IP, so a run
    without any token takes the slower throttle even though its probe passed.
    """
    canary = next((sfx for sfx, nums in existing.items() if nums), CANARY_SUFFIX)

    if os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN"):
        if _search(canary, auth=True).get("total_count"):
            return True, 2.2
        print(f"  war: token search returns no hits for {canary} (scope-limited token?)")

    if _search(canary, auth=False).get("total_count"):
        print("  using unauthenticated search (slower throttle)")
        return False, 6.5
    raise RuntimeError(
        f"search for the reference record {canary} returns no hits, "
        f"authenticated or not -- {CURATION_REPO} search looks broken"
    )


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
    """True if issue ``number`` carries the ROR ID assignment comment for ``sfx``.

    Add requests announce the assigned ROR ID in a comment like
    ``Assigned ROR ID https://ror.org/<sfx> in release v2.8.``; this is the only
    reliable, machine-readable signal that the issue produced *this* record.
    Matching is on the phrase alone -- curators and ``ror-curator-bot`` both
    post it, and the author carries no meaning.

    The needle is suffix-specific, so the check runs per ``sfx`` — only the fetched
    comment bodies are cached (by issue number), never the boolean verdict.
    """
    needle = f"Assigned ROR ID https://ror.org/{sfx}"
    return any(needle in body for body in _comment_bodies(number, cache))


def seed(existing: dict[str, list[int]], only: list[str] | None = None) -> dict[str, list[int]]:
    """Search curation issues by ROR URL; merge update + add requests into ``existing``.

    ``only`` narrows the search to the given suffixes; every other record's
    links are carried over untouched, so a narrowed run still writes a complete
    map. An empty (but not ``None``) ``only`` searches nothing -- an empty
    selection means "no records", never "all records".
    """
    merged = {k: list(v) for k, v in existing.items()}
    suffixes = _suffixes()
    if only is not None:
        wanted = set(only)
        suffixes = [sfx for sfx in suffixes if sfx in wanted]
        unknown = wanted - set(suffixes)
        if unknown:
            print(f"  war: not in records.json, skipped: {', '.join(sorted(unknown))}")
    if not suffixes:
        # Nothing selected -- return before _search_mode, whose probe would
        # otherwise spend a request, and could raise, on a run with no work.
        print("  nothing to search")
        return dict(sorted(merged.items()))
    comment_cache: dict[int, list[str]] = {}
    auth, delay = _search_mode(existing)
    for i, sfx in enumerate(suffixes, 1):
        known = set(merged.get(sfx, []))
        # Unqualified search returns title, body and comment matches in one call.
        data = _search(sfx, auth=auth)
        # Modify requests name their target in the body's "ROR ID:" field, which
        # is distinct from the "Related organizations:" URLs — so this matches the
        # target record even when the title omits the URL (a newer template does).
        rorid_field = re.compile(rf"ROR ID:\s*https?://ror\.org/{re.escape(sfx)}(?![\w-])")
        nums: set[int] = set()
        for it in data.get("items", []):
            n = it["number"]
            if n in known:
                continue  # already recorded — no need to re-classify/re-fetch
            if f"ror.org/{sfx}" in (it.get("title") or ""):
                nums.add(n)  # URL in title -> trusted update request
            elif rorid_field.search(it.get("body") or ""):
                nums.add(n)  # ROR ID field in body -> modify request for this record
            elif _has_add_release_comment(n, sfx, comment_cache):
                nums.add(n)  # URL only in comments + release announcement -> add request
        if nums:
            merged[sfx] = sorted(known | nums, reverse=True)
            print(f"[{i}/{len(suffixes)}] {sfx}: {sorted(nums, reverse=True)}")
        time.sleep(delay)
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
    parser.add_argument("--only", nargs="+", metavar="SUFFIX", help="Seed only these records (default: all)")
    parser.add_argument("--out", type=Path, default=Path("data/issues.json"), help="Enriched overlay path")
    args = parser.parse_args()

    # --only narrows the seed search; the enrich path has nothing to narrow, so
    # accepting it there would silently do a full run under a flag that reads
    # like a restriction.
    if args.only and not args.seed:
        parser.error("--only requires --seed")

    existing = json.loads(CURATION_JSON.read_text(encoding="utf-8")) if CURATION_JSON.exists() else {}

    if args.seed:
        scope = f"{len(args.only)} record(s)" if args.only else "all records"
        print(f"Seeding from {CURATION_REPO} (title search, {scope}) ...")
        mapping = seed(existing, only=args.only)
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
