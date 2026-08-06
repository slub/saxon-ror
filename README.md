# Saxon ROR

A curated, regularly updated subset of the Research Organization Registry (ROR) containing every organization located in Saxony (Sachsen), Germany — plus a small, deterministic browser to search it.

**Live site:** https://slub.github.io/saxon-ror/

Date created: July 3, 2026

Maintained by the Saxon State and University Library (SLUB) Dresden.

## What you get

This project offers two things:

1. **The data** — the unmodified, original ROR records for Saxon institutions, stored verbatim (only re-indented; no field changes, additions, or deletions).
2. **A browser** to search and view the records — case- and accent-insensitive *substring* matching across every name variant and the ROR ID, sorted alphabetically, plus a plain detail view for each record.

## What's in `data/`

The `data/` root holds the authoritative ROR subset.

| Path | Contents |
| --- | --- |
| `data/records/<ror-id-suffix>.json` | One file per organization, stored unmodified |
| `data/records.json` | The combined array of all records |
| `data/records.csv` | Convenience CSV, one row per record (names, identifiers, dates, relationships); multi-value fields are `"; "`-joined |
| `data/meta.json` | Dump version, Zenodo DOI + creation datetime, retrieval date, record counts |
| `data/history.json` | Derived overlay: each record's ROR release history (see below) |
| `data/reuse/<source>/…` | Derived companion data (see below) |

### Filter criterion

A record is included when any of its locations is in Saxony:

- **v2 schema** (current dumps): a location whose `geonames_details` has `country_code == "DE"` and `country_subdivision_name == "Saxony"` (or `country_subdivision_code == "SN"`).
- **v1 schema** (historical dumps, see backfill): an address whose `geonames_city.geonames_admin1.id == 2842566` (the GeoNames admin1 ID for Saxony), falling back to state code `DE-SN` / state name `Saxony` for a German record.

All organization types (education, funder, facility, government, healthcare, nonprofit, company, archive, other) and all statuses (active, inactive, withdrawn) are included. Neighbouring subdivisions such as *Lower Saxony* (`NI`) and *Saxony-Anhalt* (`ST`) are deliberately not matched.

## Companion sources (`data/reuse/`)

ROR is authoritative here. Other datasets that describe the same institutions live under `data/reuse/<source>/` as derived companion layers, never merged into the ROR records. The first such source is OpenAlex.

Every companion source follows the same pattern:

```
data/reuse/<source>/
├── records/<ror-id-suffix>.json   # keyed by ROR ID suffix, pairs with data/records/
└── records.json                   # combined array
```

plus its own block in `data/meta.json` (retrieval date, match statistics, data license, access terms) and a matching `scripts/update_<source>.py`.

### OpenAlex

OpenAlex institution entities are keyed by ROR ID, so each record here is fetched via `filter=ror:<id>` from the OpenAlex institutions API and stored unmodified under `data/reuse/openalex/`. Only the institution entities are kept — their built-in aggregates (`works_count`, `cited_by_count`, `counts_by_year`, `topics`) travel with the entity. No works/publication metadata is fetched.

OpenAlex data is CC0, like ROR. It is a derived layer that may lag behind or diverge from ROR — so, for example, some ROR records may have no OpenAlex counterpart. Match statistics are recorded in `data/meta.json`.

Requests always include `mailto=openalex@slub-dresden.de`; `--mailto you@example.org` overrides it. Set `OPENALEX_API_KEY` to authenticate and receive the larger API allowance — it is read from the environment only, never a command-line option. Without a key the script warns and uses anonymous access.

In CI, `update.yml` reads the repository secret of the same name, configured separately by a repository administrator. A missing secret warns and falls back anonymously; a configured but rejected key, or another API failure, fails the step.

## Data provenance

- **ROR** — original data dumps published on Zenodo under the concept DOI `10.5281/zenodo.6347574`. The scripts always resolve the concept DOI to the latest version and use the v2 schema JSON. Licensed CC0 1.0. The exact dump version, version-specific DOI, and retrieval date are recorded in `data/meta.json`.
- **OpenAlex** — fetched from the public OpenAlex institutions API. Licensed CC0 1.0. Derived companion data; ROR remains authoritative.

## Disclaimer

This is a community subset maintained by SLUB Dresden; it is **not an official ROR product**. ror.org remains the authoritative source. The data here is a filtered copy and may lag behind the live registry between updates.

## Reporting an error in a record

Records are not modified in this repository; they are a verbatim copy of the official dump. To correct an organization's data, use ROR's curation process, which flows back into the next dump and therefore into this subset.

Each record's detail page links to its ROR curation requests. `data/curation.json` maps a record to its curation issue numbers, and `scripts/update_curation.py` enriches them with live titles and states at deploy time.

Its `--seed` mode finds those numbers by searching the tracker for a record's ROR URL, keeping the hits that name the record as their target: in the title, in the body's `ROR ID:` field, or — for the request that created the record — in an assignment comment.

For issues with *this repository* specifically (the website, scripts, or the filter), please open a GitHub issue.

## Record history

A record's detail page carries a **ROR record history** card with two separate groups, because they answer different questions. *Releases* is publication provenance: which official ROR release published or changed the record. *Curation requests* are the issues that asked for a change. Some records have no linked public request, and the card then says so rather than guessing why.

`scripts/update_history.py` writes the release side to `data/history.json`, a derived overlay keyed by ROR ID suffix; the record files themselves stay verbatim. Classifications come exclusively from official ROR publication artifacts — never from Saxon ROR's own git history — but not exclusively from release-note tables. Three artifacts contribute:

| Artifact | Role |
| --- | --- |
| Release notes (`ror-community/ror-updates`) | State "added" or "updated" outright, per section |
| Record deltas (`ror-community/ror-records`) | Every record a release deployed, one directory per release |
| Dumps (`ror-community/ror-data`) | The release catalog with its dates, and the pre-v1.0 baseline |

Each section is decided on its own. A `Records added` or `Records updated` listing that matches the count the note declares for it *states* the event type, so it is used. Where a section is missing or cut short — GitHub caps a release body at 125,000 characters and truncates the second table mid-row, and the rendered page truncates identically — the release's record delta answers instead, by comparing each record against the last official state seen for it. That is an inference over official artifacts rather than an explicit statement, so it is recorded as such: every release entry carries a `provenance` map naming the artifact behind each section. Where both artifacts answer, they are cross-checked and any disagreement is reported rather than quietly resolved.

Classification is a constraint on each record rather than a sweep of each section. A validated listing is authoritative both ways — for the records it names and for the ones it omits — and so is what is known about the record's membership: ROR cannot add a record it already holds, nor update one it does not. Each artifact rules categories out, and a record is classified only when exactly one survives. Where two survive, the release leaves that record `unresolved` rather than picking the likelier reading; where none does, the conflict is reported. Nothing is ever moved into a section that a validated listing excluded it from.

Provenance is therefore section-specific and an unresolved record is record-specific, and the release entry states both. A validated listing keeps its `release-note` provenance however many records elsewhere in the same release stayed open — one record's conflict cannot unsay what a listing stated about the records it named and omitted. A delta is held to the stricter rule: it enumerates a section only once its count checks out *and* nothing was left open that might have belonged there. A release that still carries an unresolved record is reported `partial` rather than `classified` even when both sections are accounted for, so the remaining uncertainty reaches the run summary and the pull-request warning instead of being buried in a status.

Membership is therefore three-valued, because a release that declares additions without naming them makes a previously known-absent record *neither* known-absent nor known-present. Payload freshness is tracked separately: a record can be known to exist while what it last looked like is no longer known, and specializing an update into a status change needs a real before and after, so an unknown payload yields a plain `modified`. The two spread differently — an additions-only release cannot have aged any existing record's payload, while an unnamed positive `Records updated` ages every payload it could have reached. A record delta settles both again for the records it ships, so a later release can specialize once more.

Saxon ROR's own git history classifies nothing, but it is not silent either. Where the local snapshot shows a record changing in a release no official artifact answered for, that release may have added or updated it, so the record's known absence becomes unknown membership and its payload stops counting as current — the same weakening an unnamed section causes, scoped to the records local history actually names. It only ever removes knowledge: it never establishes an event, never names a change type, and never claims the records it does *not* name were left alone.

A delta may only *enumerate* a section once its own file count is corroborated by the note's declared totals, because otherwise a shortened delta would list a section confidently and leave the missing records looking untouched. A record file that is present is evidence about that record either way. The deltas are read from the repository's default branch rather than per-release tags: a tag carries only the releases up to itself, so rebuilding the whole history from tags would mean one download per release. Because the default branch is mutable, detectable inventory regressions — a lost release or a shrinking or vanished delta directory — are reported.

The same asymmetry shapes the event type. A note that lists a record under `Records updated` establishes that it was updated, and `modified` is what that yields on its own — v1.49.1 classifies exactly this way. *Specializing* an update into `inactive`, `withdrawn` or `reactivated` needs the deployed record compared against its previous state, so only those events carry a `basis` of their own.

Event types use ROR's own vocabulary: `added`, `modified`, `inactive`, `withdrawn`, `reactivated`, plus `registry-wide` and the `pre-v1.0` baseline below. Successor IDs travel with an event as a detail and are never read as a merge — ROR uses a successor for organizational continuation, closure, restructuring and the correction of duplicate records alike.

ROR released in step with GRID through September 2021 and began independent curation and semantic versioning with v1.0 in March 2022. A record already in the final pre-v1.0 dump therefore gets a `pre-v1.0` baseline — *Present before v1.0 (GRID era)* — and v1.0 appears separately only where its deployment actually added or changed that record.

Some releases — schema migrations and registry-wide patches — publish counts but no record list, and ROR ships no delta for them either. Those are `aggregate-only`: complete statements of a different shape rather than gaps. Both halves of that are required. A counts-only note beside a delta ROR did ship is not a release described in the whole: the delta is the per-record artifact, and reading it is exactly what can leave a record `unresolved`, so such a release is reported as unresolved rather than as an established aggregate. What they mean per record depends on the arithmetic. When the declared `Records updated` equals the declared `Total organizations`, the release updated everything that existed, which is a statement about each of those records: every record present at the time gets a `registry-wide` event, and records first appearing later get none. v1.58 is currently the only release that qualifies. Where the counts fall short of the registry, the note says nothing about any particular record, so the release carries no per-record entry at all — a partial registry-wide change is not a gap in any one record's provenance.

Anything still unanswered gets an explicit non-event: `unresolved` where the delta proves the record was deployed but no artifact says whether that was an addition or an update, `pending` while that release is the newest, `unavailable` once a newer one exists. All three appear on the site under *Classification notices* and are retried daily. The run summary reports every unresolved release; the data-update pull request warns only about the release being processed and about anything that lost ground since the last run, so long-standing gaps do not raise an alarm every time. Missing material never blocks the data update and never becomes a guessed event type.

## Running the update locally

The scripts use the Python standard library only (no third-party dependencies). Python 3.11+ is recommended.

```bash
# 1. Refresh the authoritative ROR subset from the latest Zenodo dump.
python scripts/update_ror.py

# 2. Refresh the OpenAlex companion layer (reads data/records.json).
export OPENALEX_API_KEY="your-key"   # optional; larger API allowance
python scripts/update_openalex.py

# 3. Refresh the release-history overlay (reads data/records.json and git).
export GITHUB_TOKEN="your-token"     # optional; higher GitHub rate limit
python scripts/update_history.py
```

`update_history.py` rebuilds the whole overlay every run: it walks the release catalog from the pre-v1.0 dump forward, so the result depends only on upstream and not on what the file already said. Output is deterministic and goes through a single atomic replace, and the write is refused outright if the catalog comes back missing releases the overlay already records.

Tests are standard-library `unittest` and never touch the network:

```bash
python -m unittest discover -s tests
```

`update_ror.py` downloads the raw dump to a temporary directory outside the repository and commits only the filtered Saxon subset; raw ROR dumps are never committed. It exits non-zero if the filtered set is empty or shrinks by more than 20% versus the previous run, guarding against an upstream schema change silently breaking the filter.

### Previewing the site

The site is plain HTML/CSS/JS with no build step. Serve the repository root and open the `www/` directory; the page resolves `data/` relative to itself:

```bash
python -m http.server 8000
# then open http://localhost:8000/www/
```

## Automation

Five workflows in `.github/workflows/`:

- **`update.yml`** polls daily (and on manual dispatch) for a new Zenodo dump. When one appears it refreshes the ROR subset, updates the release history, links any curation requests for the records that changed, refreshes the OpenAlex layer, and opens a pull request summarizing added/removed/modified records. It never pushes to `main` directly.
- **`curation-seed.yml`** re-runs curation discovery across all records weekly, catching links that only became discoverable after the dump that introduced the record. Opens a pull request only when it finds something new.
- **`history-retry.yml`** re-runs release-history classification daily, so a release ROR had not annotated when its dump landed gets picked up later. Opens a pull request only when the committed overlay changes.
- **`pages.yml`** deploys `www/` and the data files it needs to GitHub Pages, on push and weekly (so curation-request states stay current).
- **`tag-releases.yml`** tags each dump commit on `main` with its ROR version.

## Git history as a change log

The git history was backfilled from every historical ROR dump version (going back to 2022) with each commit dated to that dump's Zenodo publication date. As a result:

```bash
git log --format='%ad %s' --date=short data/records/
```

reads as a true timeline of Saxon ROR records. Note the schema switch from v1 to v2 partway through: records are stored in whatever schema their dump provided (v1 records are not converted to v2), so the large diff at the transition is honest and expected. Each dump's schema version is recorded in `data/meta.json`.

The backfill is a one-time operation (`scripts/backfill_history.py`), run manually on a dedicated branch and merged via PR; it is not part of the scheduled workflow. OpenAlex has no equivalent public snapshot history, so its companion data starts at the present.

## Repository layout

```
saxon-ror/
├── README.md
├── LICENSE                     MIT (the code)
├── data/
│   ├── LICENSE                 CC0 1.0 (the data)
│   ├── meta.json
│   ├── records.json            authoritative ROR subset
│   ├── records.csv
│   ├── records/*.json
│   ├── curation.json           record → ROR curation-request issue numbers
│   ├── history.json            record → ROR release history (derived overlay)
│   └── reuse/                  derived companion sources
│       └── openalex/
│           ├── records.json
│           └── records/*.json
├── scripts/
│   ├── ror_lib.py              shared helpers (stdlib only)
│   ├── ror_records.py          official per-release record deltas
│   ├── ror_releases.py         official release-note reader
│   ├── update_ror.py
│   ├── update_openalex.py
│   ├── update_curation.py
│   ├── update_history.py
│   └── backfill_history.py
├── tests/                      network-free unittest suite
├── www/                       static website (deployed to GitHub Pages)
└── .github/workflows/
    ├── update.yml              daily ROR refresh → pull request
    ├── curation-seed.yml       weekly curation-link reseed
    ├── history-retry.yml       daily release-history retry
    ├── pages.yml               site deployment
    └── tag-releases.yml        version tags for dump commits
```

## License

The code (scripts and website) is licensed under the [MIT License](LICENSE). The data under `data/` is dedicated to the public domain under [CC0 1.0 Universal](data/LICENSE); ROR and OpenAlex data are themselves CC0.

## References

External resources for this project, collected in one place.

**ROR**

- Website: <https://ror.org>
- REST API: <https://api.ror.org/v2/organizations>
- Schema: <https://github.com/ror-community/ror-schema>
- Curation request: <https://curation-request.ror.org>
- Curation tracker: <https://github.com/ror-community/ror-updates/issues>
- Release notes: <https://github.com/ror-community/ror-updates/releases>
- Data dumps (Zenodo, concept DOI): <https://doi.org/10.5281/zenodo.6347574>

**OpenAlex**

- Website: <https://openalex.org>
- Institutions API: <https://api.openalex.org/institutions>
- Authentication guide: <https://developers.openalex.org/guides/authentication>

**SLUB Dresden**

- Website: <https://www.slub-dresden.de>

**Saxony**

- Styleguide: <https://www.styleguide.sachsen.de>
