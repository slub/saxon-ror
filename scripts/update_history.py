#!/usr/bin/env python3
"""Generate ``data/history.json``: the ROR release history of each Saxon record.

A derived overlay, keyed by ROR id suffix, alongside the verbatim records --
never inside them. Event types come from official ROR publication artifacts and
from nothing else: release-note sections where they state the answer, the
deployed record deltas compared against prior official state where they do not. Saxon
ROR's own git history never classifies anything. See the README for the model.

Usage:
    python scripts/update_history.py                 # rescan and rewrite
    python scripts/update_history.py --report r.md   # run summary for CI
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import ror_lib as R
import ror_records as RRec
import ror_releases as RR

SCHEMA_VERSION = 2

# Event types written into the overlay. Three of them -- ``pending``,
# ``unavailable`` and ``unresolved`` -- are explicit non-event states; the seven
# that are classified events are collected in ``CLASSIFIED_EVENTS`` below.
BASELINE, ADDED, MODIFIED = "baseline", "added", "modified"
INACTIVE, WITHDRAWN, REACTIVATED = "inactive", "withdrawn", "reactivated"
# A release whose declared "Records updated" equals the declared registry size:
# it updated everything, so it *is* an event for every record that existed.
REGISTRY_WIDE = "registry-wide"
PENDING, UNAVAILABLE = "pending", "unavailable"
# The record was deployed in this release -- the delta proves that much -- but
# official evidence cannot say whether it was added or updated. Not an event:
# naming it one or the other would be the guess this state exists to avoid.
UNRESOLVED = "unresolved"
CLASSIFIED_EVENTS = (
    BASELINE, ADDED, MODIFIED, INACTIVE, WITHDRAWN, REACTIVATED, REGISTRY_WIDE
)

# Whether ROR held the record just before the release under consideration.
# ``UNKNOWN`` is not ignorance of the record, it is knowledge that an official
# artifact left its membership open -- and it propagates until one settles it.
ABSENT, PRESENT, UNKNOWN = "absent", "present", "unknown"
# ROR's own status vocabulary, not a generic "deprecated". Which section a
# record belongs to can come from a note; which of these it was cannot.
STATUS_EVENTS = (INACTIVE, WITHDRAWN, REACTIVATED)

# Where a classification came from. A release-note section states the event type
# outright; comparing a record delta against prior official state infers it.
# Both are official ROR publication artifacts -- neither is local git history.
FROM_NOTE, FROM_DELTA = "release-note", "delta-comparison"

# Release-level statuses. ``aggregate-only`` is a release ROR described in the
# whole rather than record by record; it is a complete note of another shape,
# so it produces no per-record notice at all.
CLASSIFIED, AGGREGATE_ONLY, PARTIAL = "classified", "aggregate-only", "partial"


def version_key(version: str) -> tuple[int, ...]:
    """Sort key for a ROR version, so v1.45 < v1.45.1 < v1.46 < v1.100 < v2.0.

    ``pre-v1.0`` sorts ahead of every release: its digits are v1.0's, so it
    needs the leading rank to keep it from tying with the release it precedes.
    """
    rank = 0 if version.startswith("pre-") else 1
    return (rank, *(int(part) for part in re.findall(r"\d+", version)))


def transition(previous: dict, current: dict) -> str:
    """How a release changed one record, given both official payloads.

    Never returns ``added``: whether a record is new is decided from presence
    before this is called, so an update can never be reinterpreted as an
    addition because its previous payload happened to be unavailable.

    ROR's status vocabulary is used as ROR defines it, rather than a generic
    "deprecated". A successor relationship is *not* read as a merge -- ROR uses
    successors for continuation, closure, restructuring and the correction of
    duplicate records alike -- so successors ride along as a detail on the event
    and never become an event type.
    """
    was, now = RRec.status(previous), RRec.status(current)
    if was and was != WITHDRAWN and now == WITHDRAWN:
        return WITHDRAWN
    if was == RRec.ACTIVE and now == INACTIVE:
        return INACTIVE
    if was in (INACTIVE, WITHDRAWN) and now == RRec.ACTIVE:
        return REACTIVATED
    return MODIFIED


# --- Local snapshot transitions ----------------------------------------------
#
# Used *only* to name the profiles a release nothing official answered for may
# have touched -- so their entry can say so, and so the reconstruction stops
# treating what was known about them beforehand as still current. It never
# classifies: that is for the release notes and the deployed record deltas, and
# where neither answers the state stays unresolved rather than being guessed
# from here. Weakening knowledge and establishing it are not the same act.


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=R.REPO_ROOT, check=True, capture_output=True, text=True
    ).stdout


def _suffixes_from_paths(lines) -> set[str]:
    out = set()
    for line in lines:
        name = line.strip()
        if name.startswith("data/records/") and name.endswith(".json"):
            out.add(Path(name).stem)
    return out


def snapshot_transitions() -> dict[str, set[str]]:
    """Map each dump version to the record files that changed when it landed.

    Reads the version from each commit's ``data/meta.json`` rather than its
    subject line, so a reworded commit message cannot silently misattribute a
    transition. Uncommitted work is attributed to the version currently in
    ``data/meta.json``, which is what a mid-update workflow run sees.
    """
    out: dict[str, set[str]] = {}
    shas = _git("log", "--format=%H", "--reverse", "--", "data/meta.json").split()
    for sha in shas:
        try:
            meta = json.loads(_git("show", f"{sha}:data/meta.json"))
        except (subprocess.CalledProcessError, json.JSONDecodeError):
            continue
        version = (meta.get("ror") or {}).get("dump_version")
        if not version:
            continue
        try:
            names = _git("diff", "--name-only", f"{sha}^", sha, "--", "data/records")
        except subprocess.CalledProcessError:
            # Root commit: everything it contains is new.
            names = _git("show", "--pretty=", "--name-only", sha, "--", "data/records")
        out.setdefault(version, set()).update(_suffixes_from_paths(names.splitlines()))

    working = _git("status", "--porcelain", "--", "data/records").splitlines()
    # Deletions are dropped: a record that left the subset is not in it now.
    pending_paths = [line[3:] for line in working if not line[:2].strip().startswith("D")]
    changed = _suffixes_from_paths(pending_paths)
    if changed:
        current = (R.read_meta().get("ror") or {}).get("dump_version")
        if current:
            out.setdefault(current, set()).update(changed)
    return out


# --- History assembly --------------------------------------------------------


def build_catalog(
    zenodo: list[dict],
    dump_dates: dict[str, str],
    totals: dict[str, int],
    releases: dict[str, dict],
) -> list[dict]:
    """Every release ROR published, from whichever artifact carries it.

    Zenodo is not the whole catalog: v1.0.1 ships a dump and a record delta but
    never reached the Zenodo concept, and leaving it out would let a record it
    added look as though some later release added it.
    """
    dates: dict[str, str] = {v["version"]: v["publication_date"] for v in zenodo}
    for name, date in dump_dates.items():
        dates.setdefault(name, date)
    for name in list(totals) + list(releases):
        dates.setdefault(name, "")
    return sorted(
        ({"version": n, "date": d} for n, d in dates.items()),
        key=lambda v: version_key(v["version"]),
    )


def build_history(
    suffixes: set[str],
    catalog: list[dict],
    attempts: dict[str, RR.Parsed],
    releases: dict[str, dict],
    deltas: dict[str, dict[str, dict]],
    totals: dict[str, int],
    pre_v1: dict[str, dict],
    pre_v1_date: str,
    transitions: dict[str, set[str]],
) -> dict:
    """Assemble the overlay by walking the official release catalog in order.

    Classification is decided per section, and per section by whichever official
    artifact *states* the answer: a release-note listing that matches its own
    declared count says "added" or "updated" outright, so it is used. Where a
    note section is missing or short, the record delta answers instead, by
    comparison against the last official state of that record. Where both
    answer, they are cross-checked and disagreement is surfaced, not resolved
    away.
    """
    ordered = sorted(catalog, key=lambda v: version_key(v["version"]))
    latest = ordered[-1]["version"] if ordered else None
    events: dict[str, list[dict]] = {sfx: [] for sfx in sorted(suffixes)}

    # Two different facts, kept apart. ``membership`` is whether ROR had the
    # record at all -- that decides added against updated, and a note saying
    # "updated" must never be overturned because we happen to lack the payload.
    # It is three-valued: a release that added records without naming them
    # leaves every record it could have added neither known-absent nor
    # known-present, and guessing either way invents an event.
    # ``payload`` is the last official record content seen, with None meaning
    # "known to exist, but what it looked like is no longer known"; specializing
    # an update into a status change needs a real before *and* after, so None
    # yields a plain modified rather than a guess.
    membership: dict[str, str] = {
        s: (PRESENT if s in pre_v1 else ABSENT) for s in sorted(suffixes)
    }
    present = {s for s in suffixes if s in pre_v1}
    payload: dict[str, dict | None] = {s: pre_v1[s] for s in present}
    for sfx in sorted(present):
        events[sfx].append(_event(BASELINE, RRec.PRE_V1_VERSION, pre_v1_date, None))
    index: list[dict] = [
        {
            "version": RRec.PRE_V1_VERSION,
            "date": pre_v1_date,
            "status": CLASSIFIED,
            "source": "pre-v1.0 ROR dump",
            "reason": "GRID-era presence, before ROR's independent versioning",
            "records": {"present": len(present)},
        }
    ]

    for version in ordered:
        name, date = version["version"], version["date"]
        url = RR.release_url(name) if name in releases else None
        entry = {"version": name, "date": date}
        if url:
            entry["url"] = url

        parsed = attempts.get(name)
        # Read once and used twice: what the note states per section decides the
        # categories below, and it goes on deciding them for the records only
        # local history names. The same evidence, or the two would drift apart.
        note_added, note_updated = _note_sets(parsed, suffixes)
        # Presence in ``totals`` is what says a delta exists: a release that
        # deployed records but touched none of ours still published one.
        delta = deltas.get(name, {}) if name in totals else None
        complete = _delta_complete(name, parsed, totals) if delta is not None else False
        # An aggregate-only note declares "added: 0", which would otherwise
        # validate trivially and pass as a real per-record answer.
        universal = (
            parsed is not None and delta is None and parsed.universal_aggregate
        )
        if parsed is not None and parsed.aggregate_only and delta is None:
            added, updated, open_ids, provenance, disagreements = (
                set(), set(), set(), {}, []
            )
            # It touched every record there was, so every record known to have
            # existed gets the event -- and only those: a record first seen
            # later did not exist to be updated, and one whose membership an
            # earlier release left open cannot be said to have carried it.
            if universal:
                for sfx in sorted(_with(membership, PRESENT)):
                    events[sfx].append(
                        _event(REGISTRY_WIDE, name, date, url, basis=FROM_NOTE)
                    )
        else:
            added, updated, open_ids, provenance, disagreements = _resolve(
                membership, delta, complete, note_added, note_updated
            )
        for sfx in sorted(open_ids):
            events[sfx].append(_event(UNRESOLVED, name, date, url))
        for sfx in sorted(added):
            events[sfx].append(
                _event(ADDED, name, date, url, RRec.successors(_at(delta, sfx)))
            )
        for sfx in sorted(updated):
            before, after = payload.get(sfx), _at(delta, sfx)
            if before is None or after is None:
                # No before, or no after, means no comparison -- so no status
                # specialization and no successor claim, just "it changed".
                events[sfx].append(_event(MODIFIED, name, date, url))
                continue
            kind = transition(before, after)
            events[sfx].append(
                _event(kind, name, date, url,
                       RRec.successors(after), RRec.successors(before),
                       # Which section a record belongs to can come from a note;
                       # *which kind* of update it was only ever comes from
                       # comparing the deployed record against its last state.
                       basis=FROM_DELTA if kind in STATUS_EVENTS else None)
            )
        settled = added | updated | open_ids
        for sfx in settled:
            # Whatever the release did to it, the record was deployed in it --
            # so it exists from here on even where the category stayed open.
            membership[sfx] = PRESENT
            # A named update with no delta leaves us knowing it changed but not
            # into what, so the payload is invalidated rather than carried over.
            payload[sfx] = _at(delta, sfx)

        # What a release changed without naming has to spread to the records it
        # could have reached -- and the two sections spread differently, so an
        # additions-only release must not age the payloads of records it could
        # not have touched. A narrower set would need an official artifact
        # naming one, and there is none: Saxon ROR's own git history can suggest
        # which records moved, never that the others held still.
        if _unnamed(ADDED, parsed, provenance, complete):
            for sfx in _with(membership, ABSENT) - settled:
                membership[sfx] = UNKNOWN
        if _unnamed("updated", parsed, provenance, complete):
            for sfx in (_with(membership, PRESENT) | _with(membership, UNKNOWN)) - settled:
                payload[sfx] = None

        info = _release_entry(name, parsed, delta, complete, totals, provenance,
                              disagreements, added, updated, open_ids)
        entry.update(info)
        if universal:
            entry["scope"] = "universal"
            entry["records"] = {"present": len(_with(membership, PRESENT))}
        # Any section left unanswered leaves records open, whether that is all
        # of them or one of two. An aggregate-only release answered in the
        # whole, so it is the one incomplete-looking case that carries no notice.
        if info["status"] in (PARTIAL, UNAVAILABLE):
            unresolved = PENDING if name == latest else UNAVAILABLE
            # A record already carrying an unresolved event needs no second,
            # vaguer notice about the same release.
            affected = sorted(
                (transitions.get(name, set()) & suffixes) - settled
            )
            for sfx in affected:
                events[sfx].append(_event(unresolved, name, date, url))
                # And the notice is not the end of it. Local history cannot say
                # what this release did to the record, but it does say the
                # release may have done something -- so whatever was known about
                # the record before it is no longer safe to compare a later
                # release against. Weakened, never classified: a possible
                # addition costs the record its known absence, and a possible
                # update costs it its current payload, so a later release falls
                # back to "unresolved" or to a plain "modified" rather than
                # specializing off state this one may already have moved.
                #
                # "Possible" is the operative word, and it is the official
                # artifacts that decide it, one record at a time. A validated
                # listing that omits the record already ruled its section out,
                # and membership rules out the other -- so a release can be
                # unclassifiable as a whole and still be unable to have touched
                # this particular record. Where that leaves no category at all,
                # the snapshot map is on its own, and it is not an official
                # artifact: it does not get to cost the record knowledge that
                # ROR's own listings say it kept.
                possible = _open_categories(membership[sfx], note_added, note_updated)
                if not possible:
                    disagreements.append(
                        f"{sfx}: local snapshot history names it, but the "
                        "validated note sections and its membership rule out "
                        "both categories"
                    )
                    continue
                if ADDED in possible and membership[sfx] == ABSENT:
                    membership[sfx] = UNKNOWN
                if "updated" in possible:
                    payload[sfx] = None
            if info["status"] == UNAVAILABLE:
                entry["status"] = unresolved
            # Merged, never replaced: a release can be unresolved as a whole and
            # still have settled individual records off its delta.
            entry.setdefault("records", {})["affected"] = len(affected)
            # The pass above can find disagreements after the entry was built,
            # so the list is written back rather than left to reach the entry
            # through a shared reference it may not have kept.
            if disagreements:
                entry["disagreements"] = list(disagreements)
        index.append(entry)

    for sfx in events:
        events[sfx].sort(key=lambda e: version_key(e["version"]), reverse=True)

    return {
        "schema_version": SCHEMA_VERSION,
        "source": {
            "release_notes": RR.RELEASES_INDEX_URL,
            "record_deltas": f"https://github.com/{RRec.RECORDS_REPO}",
            "dumps": f"https://github.com/{RRec.DATA_REPO}",
            "zenodo_concept_doi": R.ZENODO_CONCEPT_DOI,
        },
        "note": (
            "Derived overlay. Event types come from official ROR publication "
            "artifacts only: release-note sections that state them, and the "
            "record deltas ROR deploys per release, compared against prior "
            "official state, where they do not. Releases oldest first, each "
            "record's events newest first."
        ),
        "releases": index,
        "records": events,
    }


def _at(delta: dict[str, dict] | None, suffix: str) -> dict | None:
    return (delta or {}).get(suffix)


def _note_sets(parsed, suffixes) -> tuple[set[str] | None, set[str] | None]:
    """What a note *states* per section, or None where it does not state it."""
    if parsed is None:
        return None, None
    return (
        set(parsed.added) & suffixes if ADDED in parsed.validated else None,
        set(parsed.updated) & suffixes if "updated" in parsed.validated else None,
    )


def _delta_complete(name: str, parsed, totals: dict[str, int]) -> bool:
    """Whether the delta's own file count is corroborated by declared counts.

    Absence from a delta only means something once the delta is known to be
    whole. A shortened one would otherwise enumerate a section confidently and
    leave the missing records looking untouched.
    """
    declared = (parsed.declared if parsed else {}) or {}
    return bool(declared) and sum(declared.values()) == totals.get(name, 0)


def _with(membership: dict[str, str], state: str) -> set[str]:
    return {s for s, v in membership.items() if v == state}


def _unnamed(kind: str, parsed, provenance: dict[str, str], complete: bool) -> bool:
    """Whether this release may have acted in ``kind`` without naming who.

    Answered sections name their records outright, and so does a delta whose
    size checks out -- absence from a whole delta *is* a statement. What is left
    is a section the release declared a positive count for and then did not
    enumerate: it acted, on records it never named.

    The question is asked of one section, and ``provenance`` answers it for one
    section: a validated listing is in there whatever some other record in the
    same release left unresolved. A section that *did* name its records must
    never be read as unnamed, or the uncertainty of one record would spread to
    every record the listing explicitly excluded.

    A release that declares no count at all is a different kind of gap and is
    not one of these. Nothing official says it touched anything, so there is no
    scope to spread; that release is unresolved outright, and the per-record
    notices it carries are what say so.
    """
    if kind in provenance or complete:
        return False
    declared = (parsed.declared if parsed is not None else None) or {}
    return declared.get(kind, 0) > 0


def _open_categories(was: str, note_added, note_updated) -> set[str]:
    """The categories still open to a record neither validated listing names.

    The one place the constraints official evidence supplies are applied to a
    single record. A validated listing states the answer for the records it
    omits every bit as much as for the ones it names, so it rules its own
    section out. Membership rules out the other half: a record ROR already held
    cannot be added, one it did not hold cannot be updated. ``UNKNOWN`` rules
    out neither, which is the whole point of keeping the state.

    Both callers ask this same question. ``_resolve()`` asks it to name the one
    category left open; the snapshot-transition path asks it to learn which
    kinds of knowledge a release it cannot classify is still entitled to cost
    the record. One rule, so weakening can never outrun classifying.
    """
    possible = {ADDED, "updated"}
    if note_added is not None:
        possible.discard(ADDED)
    if note_updated is not None:
        possible.discard("updated")
    if was == PRESENT:
        possible.discard(ADDED)
    elif was == ABSENT:
        possible.discard("updated")
    return possible


def _resolve(membership, delta, complete, note_added, note_updated):
    """Assign each touched record the one category left open to it.

    Returns ``(added, updated, unresolved, provenance, disagreements)``. Rather
    than filling one section and sweeping the remainder into the other, every
    constraint official evidence supplies is applied to each record and only a
    single surviving category is written down. Two survivors mean the artifacts
    genuinely do not say which it was, and that is reported as such.

    ``provenance`` maps each section that came out *enumerated* to the artifact
    that enumerated it, and both are decided per section. A validated listing
    enumerates its own section outright: it names the records in it and, by
    omission, every record that is not. Nothing another record does can take
    that back. A delta enumerates a section only when its count checks out *and*
    no record was left open that might have belonged to it -- an unresolved
    record is precisely one the delta could not place. A record file in the
    delta is evidence about that record either way, so an unchecked delta still
    resolves the records it names.

    Openness is tracked the same way while the records are walked: the sections
    an unresolved record leaves in doubt are every section no validated listing
    already settled, and those are the ones the delta may not claim.
    """
    provenance: dict[str, str] = {}
    open_sections: set[str] = set()
    disagreements: list[str] = []
    # What each section *states*, or None where it stated nothing. Kept as one
    # mapping so every per-section decision below is made the same way.
    sections: dict[str, set[str] | None] = {ADDED: note_added, "updated": note_updated}
    # ``membership`` is keyed by exactly the records this overlay tracks, so it
    # doubles as the filter that keeps ROR-wide deltas scoped to Saxony.
    delta_ids = set(delta or {}) & set(membership)
    candidates = delta_ids | (note_added or set()) | (note_updated or set())

    added: set[str] = set()
    updated: set[str] = set()
    unresolved: set[str] = set()
    for sfx in sorted(candidates):
        was = membership.get(sfx, UNKNOWN)
        # A validated section states the answer for the records it names, and
        # for the records it omits. Neither may be overturned -- not by the
        # other section, and not by what we believe about membership.
        if note_added is not None and sfx in note_added:
            added.add(sfx)
            if was == PRESENT:
                disagreements.append(
                    f"{sfx}: note lists it as added, but it was already present"
                )
            continue
        if note_updated is not None and sfx in note_updated:
            updated.add(sfx)
            if was == ABSENT:
                disagreements.append(
                    f"{sfx}: note lists it as updated, but it was not present"
                )
            continue

        possible = _open_categories(was, note_added, note_updated)
        if possible == {ADDED}:
            added.add(sfx)
        elif possible == {"updated"}:
            updated.add(sfx)
        elif sfx in delta_ids:
            # Deployed for certain, classified not at all. Which sections that
            # leaves open is a per-section question: a validated listing has
            # already excluded this record from its own, so only the sections
            # resting on the delta are still in doubt.
            unresolved.add(sfx)
            open_sections |= {
                kind for kind, stated in sections.items() if stated is None
            }
            if not possible:
                disagreements.append(
                    f"{sfx}: deployed, but both categories are ruled out"
                )

    for kind, stated in sections.items():
        if stated is not None:
            provenance[kind] = FROM_NOTE
        elif complete and kind not in open_sections:
            provenance[kind] = FROM_DELTA

    # A whole delta is the list of what the release actually deployed, so a
    # record the note puts in a section but the deployment never shipped is the
    # two artifacts contradicting each other. The note still wins -- it is the
    # official classification -- but the conflict stays on the record.
    if complete:
        for kind, stated in sections.items():
            missing = sorted((stated or set()) - delta_ids)
            if missing:
                disagreements.append(
                    f"{kind}: note lists {missing}, absent from the release's delta"
                )
    return added, updated, unresolved, provenance, disagreements


def _release_entry(name, parsed, delta, complete, totals, provenance,
                   disagreements, added, updated, unresolved=frozenset()):
    """The release-index entry: what answered, from where, and how it checked."""
    if not provenance:
        # An aggregate note describes the whole release in place of a per-record
        # list. A release ROR shipped a delta for was not described that way:
        # the delta *is* the per-record artifact, and reading its records is
        # what may leave some of them unresolved. Calling that aggregate-only
        # would present an established answer where an open question stands.
        aggregate = parsed is not None and parsed.aggregate_only and delta is None
        if aggregate and parsed.universal_aggregate:
            reason = (
                f"every record in the registry updated "
                f"({parsed.declared.get('updated')} of {parsed.total}), no per-record list"
            )
        elif aggregate:
            reason = "counts published without a per-record list"
        elif delta is not None:
            # The delta exists but nothing corroborates its size, so absence
            # from it cannot be read as "this record was untouched". Counts that
            # disagree with the delta are a different gap from counts that were
            # never published, and the reason has to say which one it was.
            declared = (parsed.declared if parsed is not None else None) or {}
            reason = (
                f"record delta present ({totals.get(name, 0)} files), which the "
                f"declared counts ({sum(declared.values())}) do not confirm complete"
                if declared else
                f"record delta present ({totals.get(name, 0)} files) but no declared "
                "counts to confirm it is complete"
            )
        else:
            reason = "no usable release-note section and no record delta"
        entry = {"status": AGGREGATE_ONLY if aggregate else UNAVAILABLE, "reason": reason}
        if aggregate:
            entry["declared"] = dict(sorted(parsed.declared.items()))
        if delta is not None:
            entry["delta_files"] = totals.get(name, 0)
            entry["delta_complete"] = complete
        # An unchecked delta enumerates no section, yet still settles the
        # records it names one by one. Those belong in the count even though the
        # release as a whole stays unclassified.
        if added or updated or unresolved:
            entry["records"] = {"added": len(added), "updated": len(updated)}
        if unresolved:
            entry.setdefault("records", {})["unresolved"] = len(unresolved)
        if disagreements:
            entry["disagreements"] = list(disagreements)
        return entry

    # Both sections enumerated is not the whole answer. A record the release
    # demonstrably deployed and no artifact could categorise is uncertainty the
    # release still carries, and "classified" would bury it -- so it stays
    # partial, which is what puts it in the report and in the warning.
    complete_answer = len(provenance) == len(RR.KINDS) and not unresolved
    entry = {
        "status": CLASSIFIED if complete_answer else PARTIAL,
        "provenance": dict(sorted(provenance.items())),
        # "updated" rather than "modified": the section covers status
        # transitions too, and those are not plain modifications.
        "records": {"added": len(added), "updated": len(updated)},
    }
    if unresolved:
        entry["records"]["unresolved"] = len(unresolved)
    if delta is not None:
        entry["delta_files"] = totals.get(name, 0)
        entry["delta_complete"] = complete
    if parsed is not None and parsed.problems:
        entry["note_problems"] = list(parsed.problems)
    if disagreements:
        entry["disagreements"] = list(disagreements)
    return entry


def _event(
    kind: str,
    version: str,
    date: str,
    url: str | None,
    successors: list[str] | None = None,
    previous_successors: list[str] | None = None,
    basis: str | None = None,
) -> dict:
    event = {"version": version, "event": kind, "date": date}
    if url:
        event["url"] = url
    if basis:
        event["basis"] = basis
    # Only when they change, so a long-standing successor is not repeated on
    # every later event. A detail, never an interpretation.
    if successors and successors != (previous_successors or []):
        event["successors"] = successors
    return event



# --- Reporting ---------------------------------------------------------------


def count_events(history: dict) -> tuple[int, int]:
    """``(classified events, unresolved notices)`` -- never added together.

    A notice says the release could not be classified; counting it as an event
    would inflate what the overlay actually knows.
    """
    classified = notices = 0
    for entries in (history.get("records") or {}).values():
        for event in entries:
            if event["event"] in CLASSIFIED_EVENTS:
                classified += 1
            else:
                notices += 1
    return classified, notices


def render_report(history: dict, attempts: dict[str, RR.Parsed]) -> str:
    index = history["releases"]
    by_status: dict[str, list[dict]] = {}
    for rel in index:
        by_status.setdefault(rel["status"], []).append(rel)
    aggregate = by_status.get(AGGREGATE_ONLY, [])
    unresolved = [r for r in index if r["status"] in (PENDING, UNAVAILABLE, PARTIAL)]
    classified_events, notices = count_events(history)

    lines = [
        "### ROR release history",
        "",
        f"- Releases in the official catalog: {len(index)}",
        f"- Fully classified: {len(by_status.get(CLASSIFIED, []))}",
        # Two ways to land here now: a section nothing answered, or a record the
        # answered sections could not place. The label has to cover both.
        f"- Partly classified (section or record unresolved): {len(by_status.get(PARTIAL, []))}",
        f"- Aggregate-only (counts published, no per-record list): {len(aggregate)}",
        f"- Unresolved: {len(by_status.get(PENDING, [])) + len(by_status.get(UNAVAILABLE, []))}",
        f"- Classified release events across {len(history['records'])} records: {classified_events}",
        f"- Unresolved classification notices: {notices}",
    ]
    universal = [r for r in aggregate if r.get("scope") == "universal"]
    partial_scope = [r for r in aggregate if r.get("scope") != "universal"]
    if universal:
        lines += ["", "Registry-wide releases -- every record updated, so every record"
                      " present at the time carries the event:"]
        for rel in reversed(universal):
            lines.append(
                f"- `{rel['version']}`: {rel['records'].get('present', 0)} Saxon records"
            )
    if partial_scope:
        versions = ", ".join(f"`{r['version']}`" for r in reversed(partial_scope))
        lines += [
            "",
            f"Aggregate-only releases carry no per-record events by design: {versions}.",
        ]
    disagreed = [r for r in index if r.get("disagreements")]
    if disagreed:
        lines += ["", "**Sources disagree** (neither was silently preferred):"]
        for rel in reversed(disagreed):
            for text in rel["disagreements"]:
                lines.append(f"- `{rel['version']}` {text}")
    if unresolved:
        lines += [
            "",
            "| Version | State | Records | Provenance | Reason |",
            "| --- | --- | --- | --- | --- |",
        ]
        for rel in reversed(unresolved):
            reason = rel.get("reason") or attempts_reason(rel["version"], attempts) or ""
            counts = rel.get("records") or {}
            if {"added", "updated", "modified"} & set(counts):
                # "modified" is the schema-1 spelling of "updated".
                shown = counts.get("added", 0) + counts.get(
                    "updated", counts.get("modified", 0)
                )
            else:
                shown = counts.get("affected", counts.get("present", 0))
            # Records the release demonstrably touched without saying how are
            # neither classified nor merely "affected"; they get their own column
            # entry so the table cannot pass them off as either.
            if counts.get("unresolved"):
                shown = f"{shown} (+{counts['unresolved']} unresolved)"
            provenance = ", ".join(
                f"{k}: {v}" for k, v in (rel.get("provenance") or {}).items()
            ) or "-"
            lines.append(
                f"| `{rel['version']}` | {rel['status']} | {shown} | {provenance} | {reason} |"
            )
    return "\n".join(lines) + "\n"


def attempts_reason(version: str, attempts: dict[str, RR.Parsed]) -> str:
    parsed = attempts.get(version)
    if parsed is None or not parsed.problems:
        return ""
    return "; ".join(parsed.problems)


def render_warning(problems: list[str]) -> str:
    """Neutral wording on purpose.

    These warnings cover more than a failed classification: a release that is
    still classified can lose records, and one that used to validate a section
    can stop. Saying "could not be classified" would be false for those, and so
    would promising an unresolved record entry -- a lost event leaves no entry
    at all. Only the no-guessing guarantee holds in every case.
    """
    body = "\n".join(f"> - {p}" for p in problems)
    return (
        "> [!WARNING]\n"
        "> ROR release history needs review:\n"
        f"{body}\n"
        "> No missing event type was guessed. The daily `history-retry` workflow\n"
        "> retries unresolved releases.\n"
    )


# --- Main --------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=R.HISTORY_PATH, help="Overlay path")
    parser.add_argument("--report", type=Path, help="Write a run summary here (markdown)")
    parser.add_argument(
        "--warning", type=Path, help="Write a warning here, only when one is warranted"
    )
    args = parser.parse_args()

    records = json.loads((R.DATA_DIR / "records.json").read_text(encoding="utf-8"))
    suffixes = {R.ror_suffix(rec) for rec in records if rec.get("id")}
    if not suffixes:
        print("ERROR: data/records.json holds no records.", file=sys.stderr)
        return 1
    previous = json.loads(args.out.read_text(encoding="utf-8")) if args.out.exists() else {}

    print("Building the official release catalog ...")
    zenodo = R.zenodo_all_versions()
    dump_dates, pre_v1_file, pre_v1_date = RRec.dump_inventory()
    print(f"Reading release notes from {RR.RELEASES_REPO} ...")
    releases = RR.fetch_releases()

    with tempfile.TemporaryDirectory(prefix="saxon-ror-hist-") as tmp:
        tar_path = Path(tmp) / "ror-records.tar.gz"
        print(f"Fetching record deltas from {RRec.RECORDS_REPO} ...")
        R.download(RRec.RECORDS_TARBALL, tar_path)
        deltas, totals = RRec.load_deltas(tar_path, suffixes)

        dump_path = Path(tmp) / pre_v1_file
        print(f"Fetching the last pre-v1.0 dump ({pre_v1_file}) ...")
        R.download(f"{RRec.DATA_RAW}/{pre_v1_file}", dump_path)
        pre_v1 = RRec.load_pre_v1(dump_path, suffixes)

    catalog = build_catalog(zenodo, dump_dates, totals, releases)
    print(
        f"{len(catalog)} releases ({len(zenodo)} on Zenodo, {len(totals)} with a "
        f"record delta, {len(releases)} with a release note); "
        f"{len(pre_v1)} of {len(suffixes)} records present at {pre_v1_date}"
    )

    attempts: dict[str, RR.Parsed] = {}
    for version in catalog:
        release = releases.get(version["version"])
        if release is not None:
            attempts[version["version"]] = RR.classify_release(release)

    transitions = snapshot_transitions()
    history = build_history(
        suffixes, catalog, attempts, releases, deltas, totals,
        pre_v1, pre_v1_date, transitions,
    )
    try:
        write_overlay(history, args.out, previous, catalog)
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    classified_events, notices = count_events(history)
    print(
        f"Wrote {args.out}: {len(history['records'])} records, "
        f"{classified_events} classified events, {notices} unresolved notices"
    )

    report = render_report(history, attempts)
    if args.report:
        args.report.write_text(report, encoding="utf-8")
    print(report)

    if args.warning:
        problems = _warning_problems(history, attempts, previous)
        if problems:
            args.warning.write_text(render_warning(problems), encoding="utf-8")
            print(f"Wrote warning to {args.warning}")
    return 0


def inventory_problems(previous: dict, versions: list[dict]) -> list[str]:
    """Reasons the release catalog is too incomplete to rebuild the overlay.

    Every release the overlay already records must still be in the *combined*
    catalog -- not in Zenodo specifically, since some releases reach us only
    through the record deltas or the dump listing. A truncated or empty answer
    from any of those sources looks exactly like a registry that lost releases,
    which would rewrite the overlay without them and drop their events. Nothing
    detects that afterwards: a release that is absent cannot regress.
    """
    if not versions:
        return ["the official release catalog came back empty"]
    listed = {v["version"] for v in versions} | {RRec.PRE_V1_VERSION}
    known = {rel["version"] for rel in (previous.get("releases") or [])}
    missing = sorted(known - listed, key=version_key)
    if missing:
        return [
            f"the official release catalog no longer carries {len(missing)} "
            f"release(s) the overlay already records: {', '.join(missing)}"
        ]
    return []


def write_overlay(history: dict, path: Path, previous: dict, versions: list[dict]) -> None:
    """Write the overlay, unless the version inventory came back incomplete.

    The check lives here rather than at the call site so that no future path can
    reach the write without it.
    """
    problems = inventory_problems(previous, versions)
    if problems:
        raise RuntimeError(
            "; ".join(problems) + " -- refusing to rewrite the overlay from an "
            "incomplete version list"
        )
    R.dump_json_atomic(history, path)


def classified_events_by_version(history: dict) -> dict[str, set[tuple[str, str]]]:
    """``version -> {(record, event)}`` over classified events only."""
    out: dict[str, set[tuple[str, str]]] = {}
    for suffix, entries in (history.get("records") or {}).items():
        for event in entries:
            if event.get("event") in CLASSIFIED_EVENTS:
                out.setdefault(event["version"], set()).add((suffix, event["event"]))
    return out


# Statuses that mean "we know what this release says". Anything else is open.
ESTABLISHED = (CLASSIFIED, PARTIAL, AGGREGATE_ONLY)


def _sections(entry: dict) -> set[str]:
    """The sections a release entry answered for, across schema versions.

    Schema 2 records a provenance map; schema 1 recorded a ``validated`` list.
    An overlay written before the upgrade still has to be comparable, or the
    first run after it would report every release as having lost ground.
    """
    return set((entry.get("provenance") or {}).keys()) or set(entry.get("validated") or [])


# How much a provenance source establishes. A release-note section *states* the
# event type; a delta comparison infers it from prior official state. Both are
# official artifacts, so both are usable -- but they are not interchangeable,
# and moving from the first to the second is a loss.
_SOURCE_RANK = {FROM_DELTA: 1, FROM_NOTE: 2}


def _source_change(was: str, now: str) -> str | None:
    """How a section's source moved, phrased for a reader, or None if it did not.

    Only a move between two ranked sources has a direction, so only that one is
    called a weakening. A source the ranking does not recognise cannot be placed
    above or below the other -- reporting it as a loss would assert a direction
    nothing here established, and staying silent would hide the very case a
    ranking written today cannot anticipate. It is reported without a verdict.
    """
    if was == now:
        return None
    was_rank, now_rank = _SOURCE_RANK.get(was), _SOURCE_RANK.get(now)
    if was_rank is None or now_rank is None:
        return f"provenance changed from {was} to {now} (unranked source)"
    if now_rank < was_rank:
        return f"provenance weakened from {was} to {now}"
    return None


def _provenance_changes(prev: dict, entry: dict) -> list[str]:
    """Sections still answered, but not by the artifact that answered last time.

    Every other guard here stays silent for this: the section keys, the release
    status and the classified events are all unchanged, and only the artifact
    behind the answer moved. That is what a run against unreachable or
    rate-limited release notes looks like -- the overlay keeps its shape while
    knowing strictly less -- so it is reported per section, naming both sources.

    Only schema 2 records a source at all. A schema-1 ``validated`` list names
    its sections and nothing about where they came from, so there is nothing to
    compare and the upgrade must not look like a downgrade.
    """
    was = prev.get("provenance") or {}
    now = entry.get("provenance") or {}
    changes = (
        (kind, _source_change(was[kind], now[kind]))
        for kind in sorted(was.keys() & now.keys())
    )
    return [f"{kind} {change}" for kind, change in changes if change]


def _warning_problems(history: dict, attempts: dict, previous: dict) -> list[str]:
    """Releases worth interrupting a reviewer for.

    Two things qualify: the newest Zenodo version failing to settle, and any
    release *losing* ground since the last run. The long tail of releases ROR
    never published usable notes for is reported every run but must not raise an
    alarm every run.

    Regression is measured against what the overlay actually established, not
    just the release status -- a partial release that quietly stops validating a
    section, a classified one whose record list shrinks, or one whose section is
    still answered but only by the delta now, has lost information even though
    its status never moved.
    """
    latest = history["releases"][-1]["version"] if history["releases"] else None
    before = {rel["version"]: rel for rel in (previous.get("releases") or [])}
    was_events = classified_events_by_version(previous)
    now_events = classified_events_by_version(history)

    out: list[str] = []
    for rel in history["releases"]:
        version, status = rel["version"], rel["status"]
        reasons: list[str] = []

        if version == latest and status not in (CLASSIFIED, AGGREGATE_ONLY):
            reasons.append(attempts_reason(version, attempts) or rel.get("reason", status))

        prev = before.get(version)
        if prev is not None:
            was = prev.get("status")
            if was in ESTABLISHED and status not in ESTABLISHED:
                reasons.append(f"was {was}, now {status}")
            elif was == CLASSIFIED and status != CLASSIFIED:
                reasons.append(f"was fully classified, now {status}")
            # The deltas are read from the default branch, so a directory
            # shrinking is a thing that could happen rather than a thing tags
            # rule out. Nothing else would notice.
            was_files = prev.get("delta_files")
            if was_files:
                # Absent now means gone, not "unchanged" -- a delta directory
                # that disappeared is the worst version of shrinking.
                now_files = rel.get("delta_files", 0)
                if now_files < was_files:
                    reasons.append(
                        f"record delta shrank from {was_files} to {now_files} files"
                    )
            dropped = _sections(prev) - _sections(rel)
            if dropped:
                reasons.append(f"no longer validates: {', '.join(sorted(dropped))}")
            # A section that survived can still have lost its footing.
            reasons += _provenance_changes(prev, rel)

        lost = was_events.get(version, set()) - now_events.get(version, set())
        if lost:
            reasons.append(f"{len(lost)} classified event(s) disappeared")

        if reasons:
            affected = (rel.get("records") or {}).get("affected", 0)
            tail = f" -- {affected} Saxon record(s) affected" if affected else ""
            out.append(f"`{version}`: {'; '.join(reasons)}{tail}")
    return out


if __name__ == "__main__":
    raise SystemExit(main())
