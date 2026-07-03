# Saxon ROR

A curated, regularly updated subset of the [Research Organization Registry (ROR)](https://ror.org/) containing every organization located in Saxon (Sachsen), Germany — plus a small, deterministic browser to search it.

Maintained by the [Saxon State and University Library Dresden (SLUB)](https://www.slub-dresden.de/).

> **Live site:** https://slub.github.io/saxon-ror/

## Why this exists

The official ROR search is fuzzy — often to the point of being unhelpful when you know exactly which institution you are looking for. Common alternatives (e.g. OpenAlex) serve *remixed* rather than *original* ROR data.

This project offers two things the alternatives do not:

1. **The unmodified, original ROR records** for Saxon institutions, stored byte-for-byte as they appear in the official data dump (pretty-printed, but with no field changes, additions, or deletions).
2. **A deterministic, predictable search**: case- and accent-insensitive *substring* matching across every name variant and the ROR ID, sorted alphabetically. No fuzzy matching, no ranking magic. If a substring is in the data, you will find it; if it is not, you will not.

## What's in `data/`

The `data/` root holds the **authoritative ROR subset**. This repository is about ROR, so ROR is not treated as "a source" — it *is* the primary data.

| Path | Contents |
| --- | --- |
| `data/records/<ror-id-suffix>.json` | One file per organization, stored unmodified |
| `data/records.json` | The combined array of all records |
| `data/records.csv` | Convenience CSV with core fields |
| `data/meta.json` | Dump version, Zenodo DOI, retrieval date, record counts |
| `data/reuse/<source>/…` | Derived companion data (see below) |

### Filter criterion

A record is included when any of its locations is in Saxony:

- **v2 schema** (current dumps): a location whose `geonames_details` has `country_code == "DE"` and `country_subdivision_name == "Saxony"` (or `country_subdivision_code == "SN"`).
- **v1 schema** (historical dumps, see backfill): an address whose `geonames_city.geonames_admin1.id == 2842566` (the GeoNames admin1 ID for Saxony), falling back to state code `DE-SN` / state name `Saxony` for a German record.

All organization **types** (education, funder, facility, government, healthcare, nonprofit, company, archive, other) and all **statuses** (active, inactive, withdrawn) are included. Neighbouring subdivisions such as *Lower Saxony* (`NI`) and *Saxony-Anhalt* (`ST`) are deliberately **not** matched.

## Companion sources (`data/reuse/`)

ROR is authoritative here. Other datasets that describe the same institutions live under `data/reuse/<source>/` as **derived companion layers** — never merged into the ROR records. The first such source is **OpenAlex**.

Every companion source follows the same pattern:

```
data/reuse/<source>/
├── records/<ror-id-suffix>.json   # keyed by ROR ID suffix, pairs with data/records/
└── records.json                   # combined array
```

plus its own block in `data/meta.json` (retrieval date, match statistics, data license, access terms — future sources may not be CC0 or openly accessible) and a matching `scripts/update_<source>.py`. This keeps the `data/` root clean: new sources (e.g. the German [Open Access Monitor](https://open-access-monitor.de/)) can be added without restructuring.

### OpenAlex

[OpenAlex](https://openalex.org/) institution entities are keyed by ROR ID, so each record here is fetched via `filter=ror:<id>` from `https://api.openalex.org/institutions` and stored unmodified under `data/reuse/openalex/`. Only the **institution entities** are kept — their built-in aggregates (`works_count`, `cited_by_count`, `counts_by_year`, topics) travel with the entity. **No works/publication metadata is fetched.**

OpenAlex data is CC0, like ROR. It is a **derived layer that may lag behind or diverge from ROR**; some ROR records have no OpenAlex counterpart, which is normal. Match statistics are recorded in `data/meta.json`.

## Data provenance

- **ROR** — original data dumps published on Zenodo under the concept DOI [`10.5281/zenodo.6347574`](https://doi.org/10.5281/zenodo.6347574). The scripts always resolve the concept DOI to the latest version and use the v2 schema JSON. Licensed **CC0 1.0**. The exact dump version, version-specific DOI, and retrieval date are recorded in `data/meta.json`.
- **OpenAlex** — fetched from the public API at `https://api.openalex.org/institutions` (no authentication, polite pool via a `mailto` parameter). Licensed **CC0 1.0**. Derived companion data; ROR remains authoritative.

## Disclaimer

This is a **community subset maintained by SLUB Dresden. It is not an official ROR product.** [ror.org](https://ror.org/) remains the authoritative source. The data here is a filtered copy and may lag behind the live registry between updates.

## Reporting an error in a record

Records are **not modified** in this repository — they are a verbatim copy of the official dump. To correct an organization's data, use ROR's curation process, which flows back into the next dump and therefore into this subset:

- **Add or update a record:** https://curation.ror.org/
- More on curation: https://ror.org/curation/

For issues with *this repository* specifically (the website, scripts, or the filter), please open a GitHub issue.

## Running the update locally

The scripts use the Python standard library only (no third-party dependencies). Python 3.11+ is recommended.

```bash
# 1. Refresh the authoritative ROR subset from the latest Zenodo dump.
python scripts/update_ror.py

# 2. Refresh the OpenAlex companion layer (reads data/records.json).
python scripts/update_openalex.py --mailto you@example.org
```

Both scripts download the raw dump to a temporary directory **outside** the repository and commit only the filtered Saxon subset — raw ROR dumps are never committed. `update_ror.py` fails loudly (non-zero exit) if the filtered set is empty or shrinks by more than 20 % versus the previous run, guarding against an upstream schema change silently breaking the filter.

### Previewing the site

The site is plain HTML/CSS/JS with no build step. Serve the repository root and open the `www/` directory; the page resolves `data/` relative to itself:

```bash
python -m http.server 8000
# then open http://localhost:8000/www/
```

## Automation

- **`.github/workflows/update.yml`** runs monthly (and on manual dispatch), executes both update scripts, and — if the data changed — opens a **pull request** summarizing added/removed/modified records. It never pushes to `main` directly.
- **`.github/workflows/pages.yml`** deploys `www/` together with the data files it needs to GitHub Pages.

## Git history as a change log

The git history was **backfilled from every historical ROR dump version** (going back to 2022) with each commit dated to that dump's Zenodo publication date. As a result:

```bash
git log --format='%ad %s' --date=short data/records/
```

reads as a true timeline of Saxon ROR records. Note the **schema switch from v1 to v2 partway through**: records are stored in whatever schema their dump provided (v1 records are not converted to v2), so the large diff at the transition is honest and expected. Each dump's schema version is recorded in `data/meta.json`.

The backfill is a one-time operation (`scripts/backfill_history.py`), run
manually on a dedicated branch and merged via PR; it is not part of the
scheduled workflow. OpenAlex has no equivalent public snapshot history, so its
companion data starts at the present.

## Repository layout

```
saxon-ror/
├── README.md
├── LICENSE                     CC0 1.0 (matches the ROR data license)
├── data/
│   ├── meta.json
│   ├── records.json            authoritative ROR subset
│   ├── records.csv
│   ├── records/*.json
│   └── reuse/                  derived companion sources
│       └── openalex/
│           ├── records.json
│           └── records/*.json
├── scripts/
│   ├── ror_lib.py              shared helpers (stdlib only)
│   ├── update_ror.py
│   ├── update_openalex.py
│   └── backfill_history.py
├── www/                       static website (deployed to GitHub Pages)
└── .github/workflows/
    ├── update.yml
    └── pages.yml
```

## License

Both the code and the data are dedicated to the public domain under [CC0 1.0 Universal](LICENSE). ROR and OpenAlex data are themselves CC0.

## References

- ROR: <https://ror.org/> · REST API: <https://api.ror.org/v2/organizations> ·
  schema: <https://github.com/ror-community/ror-schema>
- OpenAlex institutions: <https://api.openalex.org/institutions>
