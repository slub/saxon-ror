"""Read and validate official ROR release notes.

A release note in ``ror-community/ror-updates`` is the one artifact that
*states* whether a release added or updated a record. This module fetches the
notes, parses their ``Records added`` / ``Records updated`` tables, and checks
each section against the count the note declares for it.

It is not the only source of classification: where a section is missing or cut
short, ``ror_records.py`` supplies the release's deployed record delta and
``update_history.py`` decides between them. Nothing here reads record JSON or
git differences -- see docs/record-history.md for the whole model.

Standard library only, matching the rest of ``scripts/``.
"""

from __future__ import annotations

import json
import os
import re
import urllib.request
from collections import Counter
from dataclasses import dataclass, field

import ror_lib as R

RELEASES_REPO = "ror-community/ror-updates"
RELEASES_API = f"https://api.github.com/repos/{RELEASES_REPO}/releases"
RELEASES_INDEX_URL = f"https://github.com/{RELEASES_REPO}/releases"


def release_url(version: str) -> str:
    """The canonical, user-facing release page for a ROR version."""
    return f"{RELEASES_INDEX_URL}/tag/{version}"


# --- Note parsing ------------------------------------------------------------

# A section heading. Always an ATX heading in every note format seen so far, so
# anchoring on "#" keeps the "- **Records added**: 3491" summary bullet -- which
# is *not* a heading -- out of the section scan.
_HEADING_RE = re.compile(r"^\s*#+\s*\**\s*Records\s+(added|updated)\b", re.I)
# Any ATX heading. A record section runs until the next heading of any kind, so
# a table under some later, unrelated heading is not read as more of it.
_ANY_HEADING_RE = re.compile(r"^\s*#{1,6}(?:\s|$)")
# The self-declared counts. "[^*]*" swallows the older
# "Records added since last release" wording without matching across the bold.
_COUNT_RE = re.compile(r"\*\*\s*Records\s+(added|updated)[^*]*\*\*\s*:\s*([\d,]+)", re.I)
# A table row. The ROR id column is the full URL in most notes but a bare
# suffix in the earliest ones (v1.2), and the leading pipe is optional.
_ROW_RE = re.compile(r"^\s*\|?\s*(?:https?://ror\.org/)?(0[0-9a-z]{8})\s*\|", re.I)
# The registry size the note states for itself. When an aggregate note's
# "Records updated" equals this, the release updated every record there was.
_TOTAL_RE = re.compile(r"\*\*\s*Total\s+organizations\s*\*\*\s*:\s*([\d,]+)", re.I)

KINDS = ("added", "updated")


def declared_counts(text: str) -> dict[str, int]:
    """The added/updated totals a note states about itself, when it states them."""
    out: dict[str, int] = {}
    for m in _COUNT_RE.finditer(text):
        kind = m.group(1).lower()
        out.setdefault(kind, int(m.group(2).replace(",", "")))
    return out


def declared_total(text: str) -> int | None:
    """The total organization count a note states about the registry."""
    match = _TOTAL_RE.search(text)
    return int(match.group(1).replace(",", "")) if match else None


@dataclass
class Sections:
    """The record listings of one note, and which listing headings it opened.

    ``headings`` matters on its own: a heading with no rows under it is an
    empty table, which is a very different statement from a note that never
    offered a listing at all.
    """

    added: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    headings: list[str] = field(default_factory=list)

    def rows(self) -> list[str]:
        return self.added + self.updated


def parse_sections(text: str) -> Sections:
    """ROR id suffixes listed under each ``Records added`` / ``updated`` heading."""
    out = Sections()
    listed: dict[str, list[str]] = {"added": out.added, "updated": out.updated}
    section: str | None = None
    for line in text.splitlines():
        if _ANY_HEADING_RE.match(line):
            heading = _HEADING_RE.match(line)
            section = heading.group(1).lower() if heading else None
            if section and section not in out.headings:
                out.headings.append(section)
            continue
        if section:
            row = _ROW_RE.match(line)
            if row:
                listed[section].append(row.group(1).lower())
    return out


@dataclass
class Parsed:
    """One candidate note text, parsed and checked against its own counts."""

    source: str  # "asset" | "attachment" | "body"
    added: list[str]
    updated: list[str]
    declared: dict[str, int]
    total: int | None = None  # "Total organizations", when the note states it
    problems: list[str] = field(default_factory=list)
    headings: list[str] = field(default_factory=list)
    # Sections whose listing matched the count the note declares for it. Each
    # section carries its own count, so each stands or falls on its own: a
    # complete "Records added" table is authoritative even when the "Records
    # updated" table beside it was cut off.
    validated: set[str] = field(default_factory=set)
    unreadable: bool = False  # this candidate could not be fetched
    # Set by classify_release from the release's other candidates: whether a
    # note is an aggregate statement is a fact about the release, not about one
    # file, so both flags have to be able to veto that verdict.
    sibling_unreadable: bool = False
    sibling_listed: bool = False

    @property
    def usable(self) -> bool:
        """Every section the note declares a count for checked out."""
        return not self.problems

    @property
    def universal_aggregate(self) -> bool:
        """An aggregate release that updated every record in the registry.

        ROR states this arithmetically rather than in prose: "Records updated"
        equal to "Total organizations" means the release touched the whole
        registry. An omitted "Records added" counts as zero; an explicitly
        positive one alongside that equality contradicts it, and an
        inconsistent note is not read as universal.
        """
        if not self.aggregate_only or not self.total:
            return False
        if self.declared.get("added", 0) != 0:
            return False
        return self.declared.get("updated") == self.total

    @property
    def partial(self) -> bool:
        """Some sections validated, others fell short of their declared count."""
        return bool(self.validated) and not self.usable and not self.aggregate_only

    @property
    def aggregate_only(self) -> bool:
        """Positive counts and no per-record listing offered anywhere.

        ROR publishes some releases -- schema migrations, type or language-tag
        patches -- as an aggregate statement about the whole registry, with the
        counts but no tables. That is a complete note of a different shape, not
        a truncated or missing one, so it must not be reported as a parse
        failure. A note that lists *some* records but fewer than it declares is
        a shortfall, and stays unresolved.

        The absence has to be total. A note that opens a ``Records updated``
        heading and then lists nothing under it is a broken table, not an
        aggregate statement -- so an observed heading disqualifies the verdict
        even with zero rows. So does a source that could not be read at all: a
        release whose attachment merely failed to download would otherwise look
        like one ROR chose not to enumerate.
        """
        return (
            not self.usable
            and not self.unreadable
            and not self.sibling_unreadable
            and not self.headings
            and not self.sibling_listed
            and not self.added
            and not self.updated
            and any(self.declared.values())
        )


def parse_note(
    text: str,
    source: str,
    fallback_declared: dict[str, int] | None = None,
    fallback_total: int | None = None,
) -> Parsed:
    """Parse one note text and validate it against the counts it declares.

    ``fallback_declared`` covers a note file that ships the tables without the
    summary bullets: the counts from the GitHub release body still apply. The
    two are merged per category rather than all-or-nothing, so a candidate that
    declares only one of them cannot leave the other table unvalidated.
    """
    sections = parse_sections(text)
    declared = dict(fallback_declared or {})
    declared.update(declared_counts(text))
    parsed = Parsed(
        source=source,
        added=sections.added,
        updated=sections.updated,
        declared=declared,
        total=declared_total(text) or (fallback_total if fallback_total else None),
        headings=sections.headings,
    )
    # Which sections the checks below disqualify, tracked as they are found.
    # Deriving this by searching the problem strings would make the wording of a
    # diagnostic message part of the validation logic.
    invalidated: set[str] = set()
    # A listing that repeats an id is not a listing we understand: the row count
    # would agree with the declared total while naming fewer records than it
    # claims. Collapsing to a set afterwards would hide exactly that.
    for kind in KINDS:
        repeated = sorted(
            id_ for id_, n in Counter(getattr(sections, kind)).items() if n > 1
        )
        if repeated:
            invalidated.add(kind)
            parsed.problems.append(
                f"{kind}: {len(repeated)} id(s) listed more than once "
                f"({', '.join(repeated[:3])}{'...' if len(repeated) > 3 else ''})"
            )
    # An id on both tables leaves neither able to say which section it belongs
    # to, so both are disqualified.
    crossed = sorted(set(sections.added) & set(sections.updated))
    if crossed:
        invalidated |= set(KINDS)
        parsed.problems.append(
            f"{len(crossed)} id(s) listed as both added and updated "
            f"({', '.join(crossed[:3])}{'...' if len(crossed) > 3 else ''})"
        )

    for kind in KINDS:
        listed = len(getattr(sections, kind))
        if kind in declared:
            if listed == declared[kind] and kind not in invalidated:
                parsed.validated.add(kind)
            elif listed != declared[kind]:
                parsed.problems.append(f"{kind}: {listed} listed, {declared[kind]} declared")
        elif listed or kind in sections.headings:
            # A listing we cannot check is not a listing we may use. Skipping it
            # silently would leave the note "usable" while its rows produced no
            # events at all -- a release that looks classified and says nothing.
            parsed.problems.append(f"{kind}: {listed} listed, no declared count to check")
    if not declared and not sections.rows() and not sections.headings:
        parsed.problems.append("no records listed and no counts declared")
    return parsed


# --- GitHub ------------------------------------------------------------------

_ATTACHMENT_RE = re.compile(r"https://github\.com/user-attachments/files/\d+/[^)\s]+")
# Only text-shaped assets are note candidates; anything else is not a note.
_NOTE_ASSET_SUFFIXES = (".md", ".txt", ".markdown")


def _request(url: str, accept: str) -> urllib.request.Request:
    headers = {"User-Agent": R.USER_AGENT, "Accept": accept}
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        # Authentication is optional and only buys a higher rate limit. The
        # token is read here and nowhere else, and never echoed: errors below
        # report the URL, which carries no credential.
        headers["Authorization"] = f"Bearer {token}"
        headers["X-GitHub-Api-Version"] = "2022-11-28"
    return urllib.request.Request(url, headers=headers)


def _fetch(url: str, accept: str) -> str:
    with urllib.request.urlopen(_request(url, accept), timeout=120) as resp:
        return resp.read().decode("utf-8", "replace")


def fetch_text(url: str) -> str:
    """Download a release asset or user attachment as text."""
    return _fetch(url, "text/plain, */*")


def fetch_releases(per_page: int = 100, max_pages: int = 10) -> dict[str, dict]:
    """Every published release of the curation repository, keyed by tag name."""
    out: dict[str, dict] = {}
    for page in range(1, max_pages + 1):
        url = f"{RELEASES_API}?per_page={per_page}&page={page}"
        batch = json.loads(_fetch(url, "application/vnd.github+json"))
        if not batch:
            break
        for rel in batch:
            if rel.get("draft"):
                continue
            out[rel["tag_name"]] = rel
        if len(batch) < per_page:
            break
    return out


def note_sources(release: dict) -> list[tuple[str, str | None]]:
    """``(source, url)`` for every place this release could carry its note.

    ``url`` is ``None`` for the release body, which is already in hand. Ordered
    most to least trustworthy, and the body comes last because GitHub caps it at
    125,000 characters, so a large inline table can be cut off mid-row. The cut
    is upstream of every public copy -- the rendered release page truncates at
    the same point -- so there is nowhere to recover the lost rows from. The
    per-section count check is what keeps the surviving table usable anyway.
    """
    body = release.get("body") or ""
    out: list[tuple[str, str | None]] = []
    for asset in release.get("assets") or []:
        name = (asset.get("name") or "").lower()
        if name.endswith(_NOTE_ASSET_SUFFIXES) and asset.get("browser_download_url"):
            out.append(("asset", asset["browser_download_url"]))
    for url in _ATTACHMENT_RE.findall(body):
        out.append(("attachment", url))
    out.append(("body", None))
    return out


def _rank(parsed: Parsed) -> tuple:
    """How informative a failed candidate is, best last (for ``max``).

    Validated sections come first -- they are the part we can actually use --
    then rows, then declared counts. Among candidates carrying none of those, a
    fetch failure ranks higher than an empty body: it at least names a cause.
    """
    return (
        len(parsed.validated),
        len(parsed.added) + len(parsed.updated),
        bool(parsed.declared),
        parsed.unreadable,
        -len(parsed.problems),
    )


def classify_release(release: dict, fetch=fetch_text) -> Parsed:
    """Pick the first note candidate that validates against its declared counts.

    Candidates are fetched one at a time and a fetch failure is kept as that
    candidate's problem, so a dead optional attachment cannot stop the release
    body -- often a perfectly good note -- from being tried.

    When none validates in full, the best-effort parse is returned with its
    problems intact, so callers can report *why* -- and still use whichever
    sections did check out, without ever treating the rest as authoritative.
    """
    body = release.get("body") or ""
    body_declared = declared_counts(body)
    body_total = declared_total(body)
    attempts: list[Parsed] = []
    for source, url in note_sources(release):
        if url is None:
            text = body
        else:
            try:
                text = fetch(url)
            except Exception as exc:  # noqa: BLE001 - any fetch problem is the same to us
                attempts.append(
                    Parsed(
                        source=source, added=[], updated=[], declared={},
                        problems=[f"{source} could not be fetched: {R.redact(str(exc))}"],
                        unreadable=True,
                    )
                )
                continue
        parsed = parse_note(
            text, source, fallback_declared=body_declared, fallback_total=body_total
        )
        # A note that validates is authoritative whatever happened to the
        # sources we did not need.
        if parsed.usable:
            return parsed
        attempts.append(parsed)

    # Report the candidate that got furthest; its problems name the shortfall.
    best = max(
        attempts,
        key=_rank,
        default=Parsed(
            source="body", added=[], updated=[], declared=body_declared,
            total=body_total, problems=["release carries no note text"],
        ),
    )
    siblings = [p for p in attempts if p is not best]
    best.sibling_unreadable = any(p.unreadable for p in siblings)
    best.sibling_listed = any(p.headings for p in siblings)
    return best
