# Saxon ROR

A curated, regularly updated subset of the Research Organization Registry (ROR) containing every organization located in Saxony (Sachsen), Germany — plus a small, deterministic browser to search it.

Maintained by the Saxon State and University Library (SLUB) Dresden since July 3, 2026. This is a community subset and **not an official ROR product**: ror.org remains the authoritative source, and this filtered copy may lag behind the live registry between updates.

## Quick start

**Browse:** <https://slub.github.io/saxon-ror/> — case- and accent-insensitive *substring* matching across every name variant and the ROR ID, sorted alphabetically, plus a plain detail view for each record.

**Download** — the data is deployed alongside the site and refreshed with every update:

```bash
curl -O https://slub.github.io/saxon-ror/data/records.json  # the full ROR records
curl -O https://slub.github.io/saxon-ror/data/records.csv   # one row per record
curl -O https://slub.github.io/saxon-ror/data/meta.json     # dump version, counts, retrieval date
```

The records are the original ROR records, stored verbatim: only re-indented, with no field changes, additions, or deletions. The data is CC0 1.0, the code MIT.

## What's in `data/`

| Path | Contents |
| --- | --- |
| `data/records/<ror-id-suffix>.json` | One file per organization |
| `data/records.json` | The combined array of all records |
| `data/records.csv` | Convenience CSV, one row per record (names, identifiers, dates, relationships); multi-value fields are `"; "`-joined |
| `data/meta.json` | Dump and schema version, Zenodo concept + version DOI, creation datetime, retrieval date, record counts, and one block per companion source (retrieval date, match statistics, licence, access terms) |
| `data/curation.json` | Record → ROR curation-request issue numbers (see [Reporting an error](#reporting-an-error-in-a-record)) |
| `data/history.json` | Derived overlay: each record's ROR release history (see [Record history](#record-history)) |
| `data/reuse/<source>/…` | Derived companion data (see [Companion data](#companion-data)) |

### Filter criterion

A record is included when any of its locations is in Saxony:

- **v2 schema** (current dumps): a location whose `geonames_details` has `country_code == "DE"` and `country_subdivision_name == "Saxony"` (or `country_subdivision_code == "SN"`).
- **v1 schema** (historical dumps, see backfill): an address whose `geonames_city.geonames_admin1.id == 2842566` (the GeoNames admin1 ID for Saxony), falling back to state code `DE-SN` / state name `Saxony` for a German record.

All organization types (education, funder, facility, government, healthcare, nonprofit, company, archive, other) and all statuses (active, inactive, withdrawn) are included. Neighbouring subdivisions such as *Lower Saxony* (`NI`) and *Saxony-Anhalt* (`ST`) are deliberately not matched.

## Provenance and licensing

- **ROR** — original data dumps published on Zenodo under the concept DOI `10.5281/zenodo.6347574`. The scripts always resolve the concept DOI to the latest version and use the v2 schema JSON. The exact dump version, version-specific DOI, and retrieval date are recorded in `data/meta.json`.
- **OpenAlex** — fetched from the public OpenAlex institutions API. Derived companion data; ROR remains authoritative.

Both upstream sources are CC0 1.0. The data under `data/` is likewise dedicated to the public domain under [CC0 1.0 Universal](data/LICENSE); the code (scripts and website) is licensed under the [MIT License](LICENSE).

## Reporting an error in a record

Records are not modified in this repository; they are a verbatim copy of the official dump. To correct an organization's data, use ROR's curation process, which flows back into the next dump and therefore into this subset.

Each record's detail page links to its ROR curation requests. `data/curation.json` maps a record to its curation issue numbers, and `scripts/update_curation.py` enriches them with live titles and states at deploy time. Its `--seed` mode discovers those numbers by searching the tracker for a record's ROR URL and keeping the hits that name the record as their target; the script's docstring explains how each kind of hit is judged.

For issues with *this repository* specifically (the website, scripts, or the filter), please open a GitHub issue.

## Record history

A record's detail page carries a **ROR record history** card with two separate groups, because they answer different questions. *Releases* is publication provenance: which official ROR release published or changed the record. *Curation requests* are the issues that asked for a change. Some records have no linked public request, and the card then says so rather than guessing why.

`scripts/update_history.py` writes the release side to `data/history.json`, a derived overlay keyed by ROR ID suffix; the record files themselves stay verbatim. Classifications come exclusively from official ROR publication artifacts — never from Saxon ROR's own git history — and not exclusively from release-note tables. Three artifacts contribute:

| Artifact | Role |
| --- | --- |
| Release notes (`ror-community/ror-updates`) | State "added" or "updated" outright, per section |
| Record deltas (`ror-community/ror-records`) | Every record a release deployed, one directory per release |
| Dumps (`ror-community/ror-data`) | The release catalog with its dates, and the pre-v1.0 baseline |

Where those artifacts fall silent or disagree, the overlay says so — `unresolved`, `pending` or `unavailable`, shown on the site under *Classification notices* and retried daily — rather than guessing an event type. Missing material never blocks the data update.

The full model — how each artifact is validated, when a release is `classified`, `partial` or `aggregate-only`, and how membership and payload freshness propagate — is in [docs/record-history.md](docs/record-history.md).

## Companion data

ROR is authoritative here. Other datasets that describe the same institutions live under `data/reuse/<source>/` as derived companion layers, never merged into the ROR records. Currently there is one source, OpenAlex. Every companion source follows the same pattern:

```
data/reuse/<source>/
├── records/<ror-id-suffix>.json   # keyed by ROR ID suffix, pairs with data/records/
└── records.json                   # combined array
```

plus its own block in `data/meta.json` and a matching `scripts/update_<source>.py`.

OpenAlex institution entities are keyed by ROR ID, so each record here is fetched via `filter=ror:<id>` from the OpenAlex institutions API and stored unmodified. Only the institution entities are kept — their built-in aggregates (`works_count`, `cited_by_count`, `counts_by_year`, `topics`) travel with the entity. No works/publication metadata is fetched. Being derived, the layer may lag behind or diverge from ROR — so, for example, some ROR records may have no OpenAlex counterpart. Match statistics are recorded in `data/meta.json`.

## Running the update locally

The scripts use the Python standard library only (no third-party dependencies). CI runs Python 3.12.

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

`OPENALEX_API_KEY` is read from the environment only, never as a command-line option; without it the script warns and uses anonymous access. Requests always include `mailto=openalex@slub-dresden.de`, which `--mailto you@example.org` overrides. In CI, `update.yml` reads the repository secret of the same name, configured separately by a repository administrator: a missing secret warns and falls back anonymously, while a configured but rejected key, or another API failure, fails the step.

Both refresh scripts fail loudly rather than write partial data. `update_ror.py` downloads the raw dump to a temporary directory outside the repository, commits only the filtered Saxon subset, and exits non-zero if that subset is empty or shrinks by more than 20% versus the previous run — a guard against an upstream schema change silently breaking the filter. `update_history.py` rebuilds the whole overlay every run, so the result depends only on upstream and not on what the file already said, and refuses to write at all if the release catalog comes back missing releases the overlay already records. Their module docstrings give the details.

Tests are standard-library `unittest` and never touch the network:

```bash
python -m unittest discover -s tests
```

### Previewing the site

The site is plain HTML/CSS/JS with no build step. Serve the repository root and open the `www/` directory; the page resolves `data/` relative to itself:

```bash
python -m http.server 8000
# then open http://localhost:8000/www/
```

## Automation

The workflows in `.github/workflows/`:

- **`update.yml`** polls daily (and on manual dispatch) for a new Zenodo dump. When one appears it refreshes the ROR subset, updates the release history, links any curation requests for the records that changed, refreshes the OpenAlex layer, and opens a pull request summarizing added/removed/modified records. It never pushes to `main` directly.
- **`curation-seed.yml`** re-runs curation discovery across all records weekly, catching links that only became discoverable after the dump that introduced the record.
- **`history-retry.yml`** re-runs release-history classification daily, so a release ROR had not annotated when its dump landed gets picked up later.
- **`pages.yml`** deploys `www/` and the data files it needs to GitHub Pages, on push and weekly (so curation-request states stay current).
- **`tag-releases.yml`** tags each dump commit on `main` with its ROR version.

The three data workflows open a pull request only when they actually find a change; most runs are a no-op.

## Git history as a change log

The git history was backfilled from every historical ROR dump version (going back to 2022) with each commit dated to that dump's Zenodo publication date. As a result:

```bash
git log --format='%ad %s' --date=short data/records/
```

reads as a true timeline of Saxon ROR records. Note the schema switch from v1 to v2 partway through: records are stored in whatever schema their dump provided (v1 records are not converted to v2), so the large diff at the transition is honest and expected.

The backfill is a one-time operation (`scripts/backfill_history.py`), run manually and not part of the scheduled workflow. OpenAlex has no equivalent public snapshot history, so its companion data starts at the present.

## Repository layout

```
saxon-ror/
├── README.md
├── LICENSE
├── docs/record-history.md
├── data/
│   ├── LICENSE
│   ├── meta.json
│   ├── records.json
│   ├── records.csv
│   ├── records/*.json
│   ├── curation.json
│   ├── history.json
│   └── reuse/openalex/
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
├── www/                        static website (deployed to GitHub Pages)
└── .github/workflows/
```

## References

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
