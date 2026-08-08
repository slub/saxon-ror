# The record-history model

How `scripts/update_history.py` decides what each ROR release did to each Saxon
record, and what it writes when it cannot decide. The result is
`data/history.json`, a derived overlay keyed by ROR ID suffix; the record files
themselves stay verbatim. The README gives the short version — this is the model.

The governing rule: classifications come exclusively from official ROR
publication artifacts, never from Saxon ROR's own git history. Release notes are
not the only such artifact, though. Three contribute:

| Artifact | Role |
| --- | --- |
| Release notes (`ror-community/ror-updates`) | State "added" or "updated" outright, per section |
| Record deltas (`ror-community/ror-records`) | Every record a release deployed, one directory per release |
| Dumps (`ror-community/ror-data`) | The release catalog with its dates, and the pre-v1.0 baseline |

## Deciding each section

Each section of a release is decided on its own. A `Records added` or
`Records updated` listing that matches the count the note declares for it
*states* the event type, so it is used.

Where a section is missing or cut short, the release's record delta answers
instead, by comparing each record against the last official state seen for it.
(Sections get cut short because GitHub caps a release body at 125,000
characters, truncating the second table mid-row; the rendered page truncates
identically.) That is an inference over official artifacts rather than an
explicit statement, so it is recorded as such: every release entry carries a
`provenance` map naming the artifact behind each section. Where both artifacts
answer, disagreements are reported rather than quietly resolved.

A delta may only *enumerate* a section once its own file count is corroborated
by the note's declared totals, because otherwise a shortened delta would list a
section confidently and leave the missing records looking untouched. A record
file that is present is evidence about that record either way.

## Deciding each record

Classification is a constraint on each record rather than a sweep of each
section. A validated listing is authoritative both ways — for the records it
names and for the ones it omits — and so is what is known about the record's
membership: ROR cannot add a record it already holds, nor update one it does
not. Each artifact rules categories out, and a record is classified only when
exactly one survives. Where two survive, the release leaves that record
`unresolved` rather than picking the likelier reading; where none does, the
conflict is reported. Nothing is ever moved into a section that a validated
listing excluded it from.

Provenance is therefore section-specific and an unresolved record is
record-specific, and the release entry states both. A validated listing keeps
its `release-note` provenance however many records elsewhere in the same release
stayed open — a conflict over one record does not retract what the listing
stated about the records it named and omitted.

A release is reported `classified` only when every section has provenance and no
record was left open; otherwise it is `partial`, so the remaining uncertainty
reaches the run summary and the pull-request warning instead of being buried in
a status.

## Membership and payload freshness

A record's membership is three-valued — *present*, *absent* or *unknown* —
because a release that declares additions without naming them makes a
previously known-absent record *neither* known-absent nor known-present.

What the record last looked like, its payload, is tracked separately: a record
can be known to exist while what it last looked like is no longer known, and
specializing an update into a status change needs a real before and after, so an
unknown payload yields a plain `modified`.

The two weaken differently. An additions-only release cannot have aged any
existing record's payload, while an unnamed positive `Records updated` ages
every payload it could have reached. A record delta settles both again for the
records it ships, so a later release can specialize once more.

## What local git history may do

Saxon ROR's own git history classifies nothing, but it is not silent either.
Where the local snapshot shows a record changing in a release no official
artifact answered for, that release may have added or updated it, so the
record's known absence becomes unknown membership and its payload stops counting
as current — the same weakening an unnamed section causes, scoped to the records
local history actually names. It only ever removes knowledge: it never
establishes an event, never names a change type, and never claims the records it
does *not* name were left alone.

## Reading the deltas

The deltas are read from the repository's default branch rather than per-release
tags: a tag carries only the releases up to itself, so rebuilding the whole
history from tags would mean one download per release. Because the default
branch is mutable, detectable inventory regressions — a lost release or a
shrinking or vanished delta directory — are reported.

## Event types

Event types use ROR's own vocabulary: `added`, `modified`, `inactive`,
`withdrawn`, `reactivated`, plus `registry-wide` and the `baseline` event
described below. Successor IDs travel with an event as a detail and are never
read as a merge — ROR uses a successor for organizational continuation, closure,
restructuring and the correction of duplicate records alike.

The asymmetry between stating and inferring shapes the event type too. A note
that lists a record under `Records updated` establishes that it was updated, and
`modified` is what that yields on its own. *Specializing* an update into
`inactive`, `withdrawn` or `reactivated` needs the deployed record compared
against its previous state, so only those events carry a `basis` of their own.

ROR released in step with GRID through September 2021 and began independent
curation and semantic versioning with v1.0 in March 2022. A record already in
the final pre-v1.0 dump therefore gets a `baseline` event under a synthetic
`pre-v1.0` version — *Present before v1.0 (GRID era)* — and v1.0 appears
separately only where its deployment actually added or changed that record.

## Aggregate-only releases

Some releases — schema migrations and registry-wide patches — publish counts but
no record list, and ROR ships no delta for them either. Those are
`aggregate-only`: complete statements of a different shape rather than gaps.
Both halves of that are required. Where ROR did ship a delta, the delta is the
per-record artifact and reading it is exactly what can leave a record
`unresolved`, so such a release is reported as unresolved rather than as an
established aggregate.

What an aggregate means per record depends on the arithmetic. When the declared
`Records updated` equals the declared `Total organizations`, the release updated
everything that existed, which is a statement about each of those records: every
record present at the time gets a `registry-wide` event, and records first
appearing later get none. Where the counts fall short of the registry, the note
says nothing about any particular record, so the release carries no per-record
entry at all — a partial registry-wide change is not a gap in any one record's
provenance.

## Non-events

Anything still unanswered gets an explicit non-event rather than a guess:

| Non-event | Written when |
| --- | --- |
| `unresolved` | The delta proves the record was deployed, but no artifact says whether that was an addition or an update |
| `pending` | Nothing has answered yet and that release is still the newest |
| `unavailable` | Nothing answered and a newer release now exists |

All three appear on the site under *Classification notices* and are retried
daily. The run summary reports every unresolved release; the data-update pull
request warns only about the release being processed and about anything that
lost ground since the last run, so long-standing gaps do not raise an alarm
every time. Missing material never blocks the data update and never becomes a
guessed event type.
