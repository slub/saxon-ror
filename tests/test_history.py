#!/usr/bin/env python3
"""Network-free tests for the ROR release-history overlay.

Run from the repository root:

    python -m unittest discover -s tests -v

Fixtures are minimal excerpts written in the shape of the real release notes
(full upstream notes run to hundreds of kilobytes and are not vendored here).
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import ror_lib as R  # noqa: E402
import ror_releases as RR  # noqa: E402
import update_history as UH  # noqa: E402

# --- Fixtures ----------------------------------------------------------------

PREAMBLE = """# **ROR Release {version}**
- **Total organizations**: 132,537
- **Records added**: {added}
- **Records updated**: {updated}

Access data in this release via the [ROR API](https://api.ror.org/organizations).
"""

INLINE_NOTE = PREAMBLE.format(version="v2.10", added=2, updated=1) + """
# **Records added**
ROR ID | Organization name
-- | --
https://ror.org/04ajdc372|Stiftung Hochschulmedizin Dresden
https://ror.org/00z7rqk70|Far North Queensland Hospital Foundation

# **Records updated**
ROR ID | Organization name
-- | --
https://ror.org/042aqky30|Technische Universität Dresden
"""

# The earliest notes list bare suffixes rather than full ROR URLs.
BARE_ID_NOTE = PREAMBLE.format(version="v1.2", added=0, updated=2) + """
# **Records updated**

ROR ID | Organization name
-- | --
042aqky30 | Technische Universität Dresden
0245cg223 | Something Else
"""

# The v2.9 shape: a complete "Records added" table next to an "updated" table
# the 125,000-character body cap cut short. The added rows are still official.
SECTION_SHORT_NOTE = PREAMBLE.format(version="v2.9", added=2, updated=3) + """
# **Records added**
ROR ID | Organization name
-- | --
https://ror.org/014zt3e74|All Silicon System Integration Dresden
https://ror.org/00z7rqk70|Far North Queensland Hospital Foundation

# **Records updated**
ROR ID | Organization name
-- | --
https://ror.org/0245cg223|Something Else
"""

# Both sections listed in full and both counts checking out, so each *states*
# its answer -- for the ids it names and for every id it leaves out.
BOTH_SECTIONS_NOTE = PREAMBLE.format(version="v1.17", added=1, updated=1) + """
# **Records added**
ROR ID | Organization name
-- | --
https://ror.org/0245cg223|Something Else

# **Records updated**
ROR ID | Organization name
-- | --
https://ror.org/00z7rqk70|Far North Queensland Hospital Foundation
"""

# One section listed in full and checking out beside one that was declared and
# never listed. The validated section states its answer -- for the ids it names
# and for every id it omits -- while the other states nothing at all.
UPDATED_SECTION_ONLY = PREAMBLE.format(version="v1.17", added=2, updated=1) + """
# **Records updated**
ROR ID | Organization name
-- | --
https://ror.org/0245cg223|Something Else
"""

ADDED_SECTION_ONLY = PREAMBLE.format(version="v1.17", added=1, updated=2) + """
# **Records added**
ROR ID | Organization name
-- | --
https://ror.org/0245cg223|Something Else
"""

# The same two shapes, with the other section's count never declared either. A
# declared count is a statement that the release acted in that section, so
# leaving it out is what keeps the unanswered section from spreading to every
# record it could have reached -- which isolates the per-record decision.
ADDED_SECTION_NO_UPDATE_COUNT = """# **ROR Release v1.17**
- **Total organizations**: 132,537
- **Records added**: 1

# **Records added**
ROR ID | Organization name
-- | --
https://ror.org/0245cg223|Something Else
"""

UPDATED_SECTION_NO_ADD_COUNT = """# **ROR Release v1.17**
- **Total organizations**: 132,537
- **Records updated**: 1

# **Records updated**
ROR ID | Organization name
-- | --
https://ror.org/0245cg223|Something Else
"""

# A GitHub release body capped mid-table: the rows stop, the counts do not.
TRUNCATED_BODY = PREAMBLE.format(version="v2.9", added=3, updated=2) + """
# **Records added**
ROR ID | Organization name
-- | --
https://ror.org/04ajdc372|Stiftung Hochschulmedizin Dresden
https://ror.org/00z7rqk7"""

# Registry-wide operation ROR states in the aggregate: counts, no listing.
COUNTS_ONLY_NOTE = PREAMBLE.format(version="v1.58", added=0, updated=111808)

# The v1.58 shape: "Records updated" equals "Total organizations", so the
# release updated every record that existed.
UNIVERSAL_NOTE = """# **ROR Release v1.58**
- **Total organizations**: 111,808
- **Records added**: 0
- **Records updated**: 111,808
"""
# Same, with the zero-valued "Records added" line omitted entirely.
UNIVERSAL_NO_ADDED = """# **ROR Release v1.58**
- **Total organizations**: 111,808
- **Records updated**: 111,808
"""
# Equality, but a positive "Records added" contradicts it.
UNIVERSAL_INCONSISTENT = """# **ROR Release v1.58**
- **Total organizations**: 111,808
- **Records added**: 12
- **Records updated**: 111,808
"""

NO_TABLE_NO_COUNTS = "# **ROR Release v9.9**\n\nSomething went wrong upstream.\n"

# A listing was offered and then came out empty -- a broken table, not an
# aggregate statement. The header row is deliberately not pipe-separated.
EMPTY_SECTION_NOTE = PREAMBLE.format(version="v9.8", added=0, updated=100) + """
# **Records updated**

 ROR ID    Organization name
━━━━━━━━  ━━━━━━━━━━━━━━━━━━━
"""

ZENODO = [
    {"version": "v1.0", "publication_date": "2022-03-17"},
    {"version": "v1.17", "publication_date": "2022-12-15"},
    {"version": "v1.17.1", "publication_date": "2022-12-16"},
    {"version": "v2.9", "publication_date": "2026-06-23"},
    {"version": "v2.10", "publication_date": "2026-07-20"},
]
CATALOG = [{"version": v["version"], "date": v["publication_date"]} for v in ZENODO]
PRE_V1_DATE = "2021-09-23"


def record(status="active", successors=()):
    return {
        "id": "https://ror.org/00000000x",
        "status": status,
        "relationships": [
            {"type": "successor", "id": f"https://ror.org/{s}"} for s in successors
        ],
    }


def release(tag, body="", assets=()):
    return {"tag_name": tag, "body": body, "assets": list(assets)}


def asset(name, url="https://example.invalid/notes"):
    return {"name": name, "browser_download_url": url}


# --- Note parsing ------------------------------------------------------------


class TestNoteParsing(unittest.TestCase):
    def test_inline_table(self):
        parsed = RR.parse_note(INLINE_NOTE, "body")
        self.assertTrue(parsed.usable, parsed.problems)
        self.assertEqual(parsed.added, ["04ajdc372", "00z7rqk70"])
        self.assertEqual(parsed.updated, ["042aqky30"])

    def test_bare_ids_and_spaced_pipes(self):
        parsed = RR.parse_note(BARE_ID_NOTE, "body")
        self.assertTrue(parsed.usable, parsed.problems)
        self.assertEqual(parsed.added, [])
        self.assertEqual(parsed.updated, ["042aqky30", "0245cg223"])

    def test_summary_bullet_is_not_a_section_heading(self):
        # "- **Records added**: 2" must not open a section, or the first table
        # row after it would be filed under the wrong event type.
        sections = RR.parse_sections(PREAMBLE.format(version="v1.0", added=2, updated=1))
        self.assertEqual(sections.rows(), [])
        self.assertEqual(sections.headings, [])

    def test_headings_are_recorded_even_with_no_rows_under_them(self):
        sections = RR.parse_sections(EMPTY_SECTION_NOTE)
        self.assertEqual(sections.rows(), [])
        self.assertEqual(sections.headings, ["updated"])

    def test_headings_are_recorded_in_order_of_appearance(self):
        self.assertEqual(RR.parse_sections(INLINE_NOTE).headings, ["added", "updated"])

    def test_an_unrelated_heading_closes_the_section(self):
        text = (
            "# **Records added**\nhttps://ror.org/04ajdc372|A\n"
            "# **Deprecated identifiers**\nhttps://ror.org/042aqky30|Not an addition\n"
        )
        sections = RR.parse_sections(text)
        self.assertEqual(sections.added, ["04ajdc372"])
        self.assertEqual(sections.updated, [])

    def test_a_repeated_id_within_a_section_is_rejected(self):
        text = "- **Records added**: 2\n# **Records added**\n" + (
            "https://ror.org/04ajdc372|A\nhttps://ror.org/04ajdc372|A again\n"
        )
        parsed = RR.parse_note(text, "body")
        self.assertFalse(parsed.usable)
        self.assertEqual(parsed.validated, set())
        self.assertIn("added: 1 id(s) listed more than once (04ajdc372)", parsed.problems)

    def test_a_repeated_id_in_the_updated_section_is_rejected(self):
        text = "- **Records updated**: 2\n# **Records updated**\n" + (
            "https://ror.org/042aqky30|A\nhttps://ror.org/042aqky30|A again\n"
        )
        parsed = RR.parse_note(text, "body")
        self.assertFalse(parsed.usable)
        self.assertEqual(parsed.validated, set())

    def test_an_id_in_both_sections_is_rejected(self):
        text = (
            "- **Records added**: 1\n- **Records updated**: 1\n"
            "# **Records added**\nhttps://ror.org/04ajdc372|A\n"
            "# **Records updated**\nhttps://ror.org/04ajdc372|A\n"
        )
        parsed = RR.parse_note(text, "body")
        self.assertFalse(parsed.usable)
        self.assertEqual(parsed.validated, set())
        self.assertIn("1 id(s) listed as both added and updated (04ajdc372)", parsed.problems)

    def test_declared_counts_tolerate_the_older_wording(self):
        text = "- **Records added since last release**: 167\n- **Records updated**: 205\n"
        self.assertEqual(RR.declared_counts(text), {"added": 167, "updated": 205})

    def test_truncated_table_is_a_count_mismatch(self):
        parsed = RR.parse_note(TRUNCATED_BODY, "body")
        self.assertFalse(parsed.usable)
        self.assertIn("added: 1 listed, 3 declared", parsed.problems)

    def test_partial_parse_names_the_short_section(self):
        text = PREAMBLE.format(version="v1.59", added=1, updated=2) + (
            "\n# **Records added** \nhttps://ror.org/04ajdc372|A\n"
            "\n# **Records updated** \nhttps://ror.org/042aqky30|B\n"
        )
        parsed = RR.parse_note(text, "body")
        self.assertEqual(parsed.problems, ["updated: 1 listed, 2 declared"])

    def test_counts_without_any_listing_is_aggregate_only(self):
        parsed = RR.parse_note(COUNTS_ONLY_NOTE, "body")
        self.assertFalse(parsed.usable)
        self.assertTrue(parsed.aggregate_only)

    def test_an_empty_listing_section_is_not_aggregate_only(self):
        # Declared count, a "Records updated" heading, and nothing under it.
        # The listing was offered and failed, so this stays unresolved.
        parsed = RR.parse_note(EMPTY_SECTION_NOTE, "body")
        self.assertFalse(parsed.usable)
        self.assertFalse(parsed.aggregate_only)
        self.assertEqual(parsed.headings, ["updated"])
        self.assertIn("updated: 0 listed, 100 declared", parsed.problems)

    def test_a_listing_heading_in_a_sibling_blocks_aggregate_only(self):
        body = COUNTS_ONLY_NOTE + "\n[n.md](https://github.com/user-attachments/files/9/n.md)\n"
        parsed = RR.classify_release(
            release("v1.58", body=body), fetch=lambda u: EMPTY_SECTION_NOTE
        )
        self.assertFalse(parsed.aggregate_only)
        self.assertTrue(parsed.sibling_listed or parsed.headings)

    def test_a_short_table_is_not_aggregate_only(self):
        # v1.13 / v2.9 shape: rows are present, just fewer than declared. That
        # is a shortfall in the note, not a deliberate aggregate statement.
        parsed = RR.parse_note(TRUNCATED_BODY, "body")
        self.assertFalse(parsed.aggregate_only)

    def test_zero_counts_without_a_listing_are_fine(self):
        parsed = RR.parse_note(PREAMBLE.format(version="v1.9", added=0, updated=0), "body")
        self.assertTrue(parsed.usable, parsed.problems)
        self.assertFalse(parsed.aggregate_only)

    def test_no_table_and_no_counts_is_unusable(self):
        parsed = RR.parse_note(NO_TABLE_NO_COUNTS, "body")
        self.assertFalse(parsed.usable)
        self.assertFalse(parsed.aggregate_only)

    def test_each_section_is_validated_on_its_own(self):
        # The added table matches its declared count; the updated one does not.
        # One short table must not discard the other.
        parsed = RR.parse_note(SECTION_SHORT_NOTE, "body")
        self.assertFalse(parsed.usable)
        self.assertTrue(parsed.partial)
        self.assertEqual(parsed.validated, {"added"})
        self.assertEqual(parsed.problems, ["updated: 1 listed, 3 declared"])
        self.assertIn("014zt3e74", parsed.added)

    def test_a_fully_matching_note_validates_every_section(self):
        parsed = RR.parse_note(INLINE_NOTE, "body")
        self.assertEqual(parsed.validated, {"added", "updated"})
        self.assertFalse(parsed.partial)

    def test_a_section_without_a_declared_count_is_unresolved(self):
        # Rows we cannot check against a count must not slip through as a
        # "classified" release that then emits no events for them.
        text = "# **Records added** \nhttps://ror.org/04ajdc372|A\n"
        parsed = RR.parse_note(text, "body")
        self.assertEqual(parsed.validated, set())
        self.assertFalse(parsed.usable)
        self.assertIn("added: 1 listed, no declared count to check", parsed.problems)

    def test_an_uncheckable_section_beside_a_valid_one_is_still_unresolved(self):
        text = "- **Records updated**: 1\n" + (
            "# **Records added** \nhttps://ror.org/04ajdc372|A\n"
            "# **Records updated** \nhttps://ror.org/042aqky30|B\n"
        )
        parsed = RR.parse_note(text, "body")
        self.assertEqual(parsed.validated, {"updated"})
        self.assertFalse(parsed.usable)
        self.assertTrue(parsed.partial)

    def test_an_empty_heading_without_a_count_is_unresolved(self):
        parsed = RR.parse_note("# **Records updated** \n", "body")
        self.assertFalse(parsed.usable)
        self.assertEqual(parsed.problems, ["updated: 0 listed, no declared count to check"])

    def test_aggregate_only_is_not_reported_as_partial(self):
        # Declares added: 0, which trivially "matches" an absent table.
        parsed = RR.parse_note(COUNTS_ONLY_NOTE, "body")
        self.assertTrue(parsed.aggregate_only)
        self.assertFalse(parsed.partial)

    def test_declared_counts_merge_per_category(self):
        # The candidate declares only "added"; "updated" must still be checked
        # against the body's count instead of going unvalidated.
        text = "- **Records added**: 1\n# **Records added** \nhttps://ror.org/04ajdc372|A\n"
        parsed = RR.parse_note(text, "asset", fallback_declared={"added": 9, "updated": 7})
        self.assertEqual(parsed.declared, {"added": 1, "updated": 7})
        self.assertEqual(parsed.problems, ["updated: 0 listed, 7 declared"])


# --- Candidate selection -----------------------------------------------------


class TestReleaseSources(unittest.TestCase):
    def test_release_asset_wins_over_a_truncated_body(self):
        rel = release("v2.9", body=TRUNCATED_BODY, assets=[asset("v2.9-release_notes.md")])
        fetched = []

        def fetch(url):
            fetched.append(url)
            return INLINE_NOTE

        parsed = RR.classify_release(rel, fetch=fetch)
        self.assertTrue(parsed.usable, parsed.problems)
        self.assertEqual(parsed.source, "asset")
        self.assertEqual(fetched, ["https://example.invalid/notes"])

    def test_linked_user_attachment(self):
        body = PREAMBLE.format(version="v2.10", added=2, updated=1) + (
            "\n[2026-07-20-v2.10-release_notes.md]"
            "(https://github.com/user-attachments/files/30180559/notes.md)\n"
        )
        parsed = RR.classify_release(release("v2.10", body=body), fetch=lambda u: INLINE_NOTE)
        self.assertTrue(parsed.usable, parsed.problems)
        self.assertEqual(parsed.source, "attachment")
        self.assertIn("04ajdc372", parsed.added)

    def test_attachment_without_its_own_counts_is_still_validated(self):
        body = PREAMBLE.format(version="v2.10", added=2, updated=1) + (
            "\n[notes.md](https://github.com/user-attachments/files/1/notes.md)\n"
        )
        tables = INLINE_NOTE.split("\n# **Records added**", 1)[1]
        parsed = RR.classify_release(
            release("v2.10", body=body), fetch=lambda u: "# **Records added**" + tables
        )
        self.assertTrue(parsed.usable, parsed.problems)
        self.assertEqual(parsed.declared, {"added": 2, "updated": 1})

    def test_non_note_assets_are_ignored(self):
        rel = release("v2.10", body=INLINE_NOTE, assets=[asset("checksums.zip")])
        calls = []
        parsed = RR.classify_release(rel, fetch=lambda u: calls.append(u))
        self.assertEqual(parsed.source, "body")
        self.assertEqual(calls, [])

    def test_unusable_everywhere_keeps_the_problems(self):
        calls = []
        parsed = RR.classify_release(
            release("v2.9", body=TRUNCATED_BODY), fetch=lambda u: calls.append(u)
        )
        self.assertFalse(parsed.usable)
        self.assertTrue(parsed.problems)
        self.assertEqual(calls, [])

    def test_a_broken_attachment_does_not_stop_the_body_from_being_tried(self):
        body = INLINE_NOTE + "\n[gone.md](https://github.com/user-attachments/files/9/gone.md)\n"

        def fetch(url):
            raise OSError("HTTP Error 404: Not Found")

        parsed = RR.classify_release(release("v2.10", body=body), fetch=fetch)
        self.assertTrue(parsed.usable, parsed.problems)
        self.assertEqual(parsed.source, "body")
        self.assertIn("04ajdc372", parsed.added)

    def test_a_fetch_failure_is_kept_as_that_candidates_problem(self):
        body = TRUNCATED_BODY + "\n[gone.md](https://github.com/user-attachments/files/9/gone.md)\n"

        def fetch(url):
            raise OSError("HTTP Error 500")

        parsed = RR.classify_release(release("v2.9", body=body), fetch=fetch)
        self.assertFalse(parsed.usable)
        # The readable candidate is reported, but the miss is not forgotten.
        self.assertEqual(parsed.source, "body")
        self.assertFalse(parsed.unreadable)
        self.assertTrue(parsed.sibling_unreadable)

    def test_an_unreachable_source_blocks_the_aggregate_only_verdict(self):
        # Counts, no table, and an attachment that may well have held one:
        # calling that "stated in the aggregate" would bury a download failure.
        body = COUNTS_ONLY_NOTE + "\n[n.md](https://github.com/user-attachments/files/9/n.md)\n"

        def fetch(url):
            raise OSError("HTTP Error 503")

        parsed = RR.classify_release(release("v1.58", body=body), fetch=fetch)
        self.assertFalse(parsed.aggregate_only)
        self.assertTrue(parsed.sibling_unreadable)

        # Same note, nothing unreachable: a genuine aggregate statement.
        clean = RR.classify_release(release("v1.58", body=COUNTS_ONLY_NOTE), fetch=fetch)
        self.assertTrue(clean.aggregate_only)

    def test_an_unreadable_only_release_reports_the_fetch_failure(self):
        rel = release("v2.9", body="", assets=[asset("v2.9-release_notes.md")])
        parsed = RR.classify_release(rel, fetch=lambda u: (_ for _ in ()).throw(OSError("boom")))
        self.assertFalse(parsed.usable)
        self.assertTrue(parsed.unreadable)
        self.assertFalse(parsed.aggregate_only)
        self.assertIn("asset could not be fetched: boom", parsed.problems)


# --- History assembly --------------------------------------------------------


def build(attempts, releases, baseline=frozenset(), transitions=None, suffixes=None,
          deltas=None, totals=None, catalog=None):
    """Assemble an overlay the way main() does, with everything injectable.

    ``baseline`` names the records the pre-v1.0 dump held; ``deltas`` maps a
    version to ``{suffix: record}`` and implies that version published a delta.
    """
    deltas = deltas or {}
    totals = totals if totals is not None else {v: len(d) for v, d in deltas.items()}
    return UH.build_history(
        suffixes if suffixes is not None else {"04ajdc372", "042aqky30", "0245cg223"},
        catalog if catalog is not None else CATALOG,
        attempts,
        releases,
        deltas,
        totals,
        {s: record() for s in baseline},
        PRE_V1_DATE,
        transitions or {},
    )


def events(history, suffix):
    return history["records"][suffix]


class TestHistoryAssembly(unittest.TestCase):
    def setUp(self):
        self.v210 = RR.parse_note(INLINE_NOTE, "body")
        self.assertTrue(self.v210.usable)

    def test_added_event_carries_zenodo_date_and_github_url(self):
        history = build({"v2.10": self.v210}, {"v2.10": {}})
        self.assertEqual(
            events(history, "04ajdc372"),
            [
                {
                    "version": "v2.10",
                    "event": "added",
                    "date": "2026-07-20",
                    "url": "https://github.com/ror-community/ror-updates/releases/tag/v2.10",
                }
            ],
        )

    def test_modified_event(self):
        history = build({"v2.10": self.v210}, {"v2.10": {}})
        self.assertEqual([e["event"] for e in events(history, "042aqky30")], ["modified"])

    def test_baseline_is_grid_era_presence_not_a_v1_0_event(self):
        v10 = RR.parse_note(PREAMBLE.format(version="v1.0", added=0, updated=0), "body")
        history = build({"v1.0": v10}, {"v1.0": {}}, baseline={"042aqky30"})
        entry = events(history, "042aqky30")
        self.assertEqual([(e["version"], e["event"]) for e in entry],
                         [("pre-v1.0", "baseline")])
        self.assertEqual(entry[0]["date"], PRE_V1_DATE)
        self.assertNotIn("url", entry[0])
        # A record the pre-v1.0 dump did not hold gets no baseline at all.
        self.assertEqual(events(history, "0245cg223"), [])

    def test_v1_0_is_an_ordinary_release_for_a_grid_era_record(self):
        # Present before v1.0 *and* touched by it: two events, not one.
        note = PREAMBLE.format(version="v1.0", added=0, updated=1) + (
            "\n# **Records updated** \nhttps://ror.org/042aqky30|TUD\n"
        )
        history = build({"v1.0": RR.parse_note(note, "body")}, {"v1.0": {}},
                        baseline={"042aqky30"})
        self.assertEqual([(e["version"], e["event"]) for e in events(history, "042aqky30")],
                         [("v1.0", "modified"), ("pre-v1.0", "baseline")])

    def test_a_record_added_after_the_grid_era_has_no_baseline(self):
        note = PREAMBLE.format(version="v1.0", added=1, updated=0) + (
            "\n# **Records added** \nhttps://ror.org/04ajdc372|A\n"
        )
        v10 = RR.parse_note(note, "body")
        history = build({"v1.0": v10}, {"v1.0": {}}, baseline={"042aqky30"})
        self.assertEqual([(e["version"], e["event"]) for e in events(history, "04ajdc372")],
                         [("v1.0", "added")])
        self.assertEqual([e["event"] for e in events(history, "042aqky30")], ["baseline"])

    def test_baseline_survives_an_unclassifiable_v1_0_note(self):
        history = build(
            {}, {"v1.0": {}, "v2.10": {}}, baseline={"042aqky30"},
            transitions={"v1.0": {"042aqky30", "0245cg223"}},
        )
        # The GRID-era fact comes from the dump, not from v1.0's note.
        self.assertEqual([e["event"] for e in events(history, "042aqky30")],
                         ["unavailable", "baseline"])
        self.assertEqual([e["event"] for e in events(history, "0245cg223")], ["unavailable"])

    def test_events_predating_local_membership_are_kept(self):
        # 042aqky30 is only touched by v1.17 here, long before any local
        # snapshot transition mentions it -- the note is the authority.
        note = PREAMBLE.format(version="v1.17", added=0, updated=1) + (
            "\n# **Records updated** \nhttps://ror.org/042aqky30|TUD\n"
        )
        history = build({"v1.17": RR.parse_note(note, "body")}, {"v1.17": {}}, transitions={})
        self.assertEqual([e["version"] for e in events(history, "042aqky30")], ["v1.17"])

    def test_events_are_newest_first(self):
        older = PREAMBLE.format(version="v1.17", added=0, updated=1) + (
            "\n# **Records updated** \nhttps://ror.org/042aqky30|TUD\n"
        )
        history = build(
            {"v1.17": RR.parse_note(older, "body"), "v2.10": self.v210},
            {"v1.17": {}, "v2.10": {}},
        )
        self.assertEqual([e["version"] for e in events(history, "042aqky30")], ["v2.10", "v1.17"])

    def test_latest_unresolved_version_is_pending(self):
        history = build({}, {"v2.10": {}}, transitions={"v2.10": {"042aqky30"}})
        self.assertEqual([e["event"] for e in events(history, "042aqky30")], ["pending"])
        self.assertEqual(history["releases"][-1]["status"], "pending")

    def test_older_unresolved_version_is_unavailable(self):
        history = build(
            {"v2.10": self.v210},
            {"v2.9": {}, "v2.10": {}},
            transitions={"v2.9": {"042aqky30"}},
        )
        self.assertEqual(
            [e["event"] for e in events(history, "042aqky30")], ["modified", "unavailable"]
        )

    def test_unresolved_state_is_replaced_once_a_note_becomes_usable(self):
        transitions = {"v2.9": {"042aqky30"}}
        before = build({}, {"v2.9": {}, "v2.10": {}}, transitions=transitions)
        self.assertEqual([e["event"] for e in events(before, "042aqky30")], ["unavailable"])
        note = PREAMBLE.format(version="v2.9", added=0, updated=1) + (
            "\n# **Records updated** \nhttps://ror.org/042aqky30|TUD\n"
        )
        after = build(
            {"v2.9": RR.parse_note(note, "body")},
            {"v2.9": {}, "v2.10": {}},
            transitions=transitions,
        )
        self.assertEqual([e["event"] for e in events(after, "042aqky30")], ["modified"])

    def test_missing_github_release_invents_nothing(self):
        # v1.17.1 exists on Zenodo only, and changed no Saxon record.
        history = build({"v2.10": self.v210}, {"v2.10": {}}, transitions={})
        self.assertNotIn("v1.17.1", [e["version"] for e in events(history, "042aqky30")])
        entry = next(r for r in history["releases"] if r["version"] == "v1.17.1")
        self.assertEqual(entry["status"], "unavailable")
        self.assertEqual(entry["records"]["affected"], 0)
        self.assertNotIn("url", entry)

    def test_aggregate_only_release_creates_no_per_record_notice(self):
        # v1.58 shape: a registry-wide operation stated in the aggregate. The
        # local snapshot did change these records, and that must stay invisible
        # here -- it is a release-wide representation change, not a gap in any
        # one record's provenance.
        aggregate = RR.parse_note(COUNTS_ONLY_NOTE, "body")
        self.assertTrue(aggregate.aggregate_only)
        history = build(
            {"v1.17": aggregate, "v2.10": self.v210},
            {"v1.17": {}, "v2.10": {}},
            transitions={"v1.17": {"042aqky30", "0245cg223"}},
        )
        self.assertEqual([e["event"] for e in events(history, "042aqky30")], ["modified"])
        self.assertEqual(events(history, "0245cg223"), [])
        entry = next(r for r in history["releases"] if r["version"] == "v1.17")
        self.assertEqual(entry["status"], "aggregate-only")
        self.assertEqual(entry["declared"], {"added": 0, "updated": 111808})

    def test_aggregate_only_latest_version_is_not_pending(self):
        history = build(
            {"v2.10": RR.parse_note(COUNTS_ONLY_NOTE, "body")},
            {"v2.10": {}},
            transitions={"v2.10": {"042aqky30"}},
        )
        self.assertEqual(history["releases"][-1]["status"], "aggregate-only")
        self.assertEqual(events(history, "042aqky30"), [])

    def test_a_validated_section_classifies_even_when_the_other_is_short(self):
        # The v2.9 case: 014zt3e74 is in a complete "Records added" table, so it
        # gets its event. 042aqky30 is only known from the local snapshot, and
        # the "Records updated" table is short, so it keeps an honest notice.
        parsed = RR.parse_note(SECTION_SHORT_NOTE, "body")
        history = build(
            {"v2.9": parsed, "v2.10": self.v210},
            {"v2.9": {}, "v2.10": {}},
            suffixes={"014zt3e74", "042aqky30"},
            transitions={"v2.9": {"014zt3e74", "042aqky30"}},
        )
        self.assertEqual(
            [(e["version"], e["event"]) for e in events(history, "014zt3e74")],
            [("v2.9", "added")],
        )
        self.assertEqual(
            [(e["version"], e["event"]) for e in events(history, "042aqky30")],
            [("v2.10", "modified"), ("v2.9", "unavailable")],
        )

        entry = next(r for r in history["releases"] if r["version"] == "v2.9")
        self.assertEqual(entry["status"], "partial")
        self.assertEqual(entry["provenance"], {"added": "release-note"})
        # The other way into "partial": a section nothing answered. The summary
        # label covers this case and the unresolved-record one alike.
        self.assertIn("Partly classified (section or record unresolved): 1",
                      UH.render_report(history, {}))

    def test_json_differences_never_classify(self):
        # The local snapshot says these records changed; without an official
        # note that yields an unresolved state, never "added" or "modified".
        history = build(
            {}, {"v2.9": {}, "v2.10": {}}, transitions={"v2.10": {"04ajdc372", "042aqky30"}}
        )
        kinds = {e["event"] for evs in history["records"].values() for e in evs}
        self.assertTrue(kinds <= {"pending", "unavailable"}, kinds)

    def test_record_keys_match_the_current_subset(self):
        history = build({"v2.10": self.v210}, {"v2.10": {}}, suffixes={"042aqky30"})
        self.assertEqual(set(history["records"]), {"042aqky30"})

    def test_output_is_deterministic_and_idempotent(self):
        args = ({"v2.10": self.v210}, {"v2.10": {}})
        first = json.dumps(build(*args, transitions={"v2.9": {"042aqky30"}}), sort_keys=False)
        second = json.dumps(build(*args, transitions={"v2.9": {"042aqky30"}}), sort_keys=False)
        self.assertEqual(first, second)
        history = build(*args)
        self.assertEqual(
            [r["version"] for r in history["releases"]],
            ["pre-v1.0", "v1.0", "v1.17", "v1.17.1", "v2.9", "v2.10"],
        )
        self.assertEqual(list(history["records"]), sorted(history["records"]))

    def test_version_ordering(self):
        versions = ["v1.45.1", "v2.0", "v1.5", "v1.45", "v1.10"]
        self.assertEqual(
            sorted(versions, key=UH.version_key),
            ["v1.5", "v1.10", "v1.45", "v1.45.1", "v2.0"],
        )


class TestStatusTransitions(unittest.TestCase):
    """The matrix, spelled out: ROR's vocabulary, not a generic "deprecated"."""

    def test_transition_never_returns_added(self):
        # Whether a record is new is decided from presence before this runs, so
        # a missing prior payload can never turn an update into an addition.
        for was in ("active", "inactive", "withdrawn", ""):
            self.assertNotEqual(UH.transition(record(was), record()), "added", was)

    def test_any_known_status_to_withdrawn_is_withdrawn(self):
        for was in ("active", "inactive"):
            self.assertEqual(
                UH.transition(record(was), record("withdrawn")), "withdrawn", was
            )

    def test_withdrawn_to_withdrawn_is_not_a_transition(self):
        self.assertEqual(UH.transition(record("withdrawn"), record("withdrawn")), "modified")

    def test_active_to_inactive_is_inactive(self):
        self.assertEqual(UH.transition(record("active"), record("inactive")), "inactive")

    def test_inactive_or_withdrawn_back_to_active_is_reactivated(self):
        for was in ("inactive", "withdrawn"):
            self.assertEqual(
                UH.transition(record(was), record("active")), "reactivated", was
            )

    def test_everything_else_is_modified(self):
        self.assertEqual(UH.transition(record("active"), record("active")), "modified")
        self.assertEqual(UH.transition(record("inactive"), record("inactive")), "modified")

    def test_a_status_event_records_that_it_came_from_the_delta(self):
        # The section may be note-classified; the *kind* of update never is.
        note = PREAMBLE.format(version="v2.10", added=0, updated=1) + (
            "\n# **Records updated** \nhttps://ror.org/042aqky30|TUD\n"
        )
        history = build(
            {"v2.10": RR.parse_note(note, "body")}, {"v2.10": {}},
            baseline={"042aqky30"},
            deltas={"v2.10": {"042aqky30": record("withdrawn")}}, totals={"v2.10": 1},
        )
        event = events(history, "042aqky30")[0]
        self.assertEqual(event["event"], "withdrawn")
        self.assertEqual(event["basis"], "delta-comparison")
        entry = next(r for r in history["releases"] if r["version"] == "v2.10")
        # "Records added: 0" is itself a statement, so both sections are answered.
        self.assertEqual(entry["provenance"],
                         {"added": "release-note", "updated": "release-note"})
        self.assertEqual(entry["records"], {"added": 0, "updated": 1})

    def test_successors_ride_along_without_being_interpreted(self):
        history = build(
            {}, {"v2.10": {}},
            deltas={"v2.10": {"042aqky30": record("withdrawn", successors=["04ajdc372"])}},
            totals={"v2.10": 1},
        )
        # No delta counts to check against, so the section is not enumerated --
        # but the record file present is still evidence about that record.
        event = events(history, "042aqky30")[0]
        self.assertEqual(event["successors"], ["04ajdc372"])
        self.assertNotIn("merged", json.dumps(history))


class TestUniversalAggregate(unittest.TestCase):
    """"Records updated" equal to the registry size is an event for everyone."""

    def test_explicit_zero_added_is_universal(self):
        parsed = RR.parse_note(UNIVERSAL_NOTE, "body")
        self.assertTrue(parsed.aggregate_only)
        self.assertTrue(parsed.universal_aggregate)
        self.assertEqual(parsed.total, 111808)

    def test_an_omitted_added_count_reads_as_zero(self):
        parsed = RR.parse_note(UNIVERSAL_NO_ADDED, "body")
        self.assertTrue(parsed.universal_aggregate)

    def test_a_positive_added_count_contradicts_the_equality(self):
        parsed = RR.parse_note(UNIVERSAL_INCONSISTENT, "body")
        self.assertTrue(parsed.aggregate_only)
        self.assertFalse(parsed.universal_aggregate)

    def test_a_non_universal_aggregate_stays_eventless(self):
        # Updated far short of the registry size: still aggregate, not universal.
        parsed = RR.parse_note(COUNTS_ONLY_NOTE, "body")
        self.assertTrue(parsed.aggregate_only)
        self.assertFalse(parsed.universal_aggregate)
        history = build({"v1.17": parsed}, {"v1.17": {}}, baseline={"042aqky30"})
        self.assertEqual([e["event"] for e in events(history, "042aqky30")], ["baseline"])

    def test_the_event_reaches_only_records_already_present(self):
        universal = RR.parse_note(UNIVERSAL_NOTE, "body")
        added_later = PREAMBLE.format(version="v2.10", added=1, updated=0) + (
            "\n# **Records added** \nhttps://ror.org/04ajdc372|A\n"
        )
        history = build(
            {"v1.17": universal, "v2.10": RR.parse_note(added_later, "body")},
            {"v1.17": {}, "v2.10": {}},
            baseline={"042aqky30"},
        )
        # Present at v1.17, so it was updated by it.
        self.assertEqual(
            [(e["version"], e["event"]) for e in events(history, "042aqky30")],
            [("v1.17", "registry-wide"), ("pre-v1.0", "baseline")],
        )
        # First seen at v2.10: it did not exist to be updated at v1.17.
        self.assertEqual(
            [(e["version"], e["event"]) for e in events(history, "04ajdc372")],
            [("v2.10", "added")],
        )

    def test_the_event_states_the_note_as_its_basis(self):
        history = build({"v1.17": RR.parse_note(UNIVERSAL_NOTE, "body")},
                        {"v1.17": {}}, baseline={"042aqky30"})
        event = events(history, "042aqky30")[0]
        self.assertEqual(event["basis"], "release-note")
        entry = next(r for r in history["releases"] if r["version"] == "v1.17")
        self.assertEqual(entry["status"], "aggregate-only")
        self.assertEqual(entry["scope"], "universal")
        self.assertEqual(entry["records"], {"present": 1})


class TestUnknownMembership(unittest.TestCase):
    """Three-valued membership: an unnamed addition leaves records neither in
    nor out, and nothing may quietly resolve that but official evidence."""

    def _counts(self, version, added, updated):
        """A note that declares counts and lists nothing -- an unnamed section."""
        return RR.parse_note(
            f"# **ROR Release {version}**\n"
            f"- **Total organizations**: 100,000\n"
            f"- **Records added**: {added}\n"
            f"- **Records updated**: {updated}\n",
            "body",
        )

    def test_an_unnamed_addition_blocks_a_later_added_verdict(self):
        # v1.17 adds one record without naming it, so X stops being known-absent.
        # v2.9 then declares "added: 0" and ships X in a complete delta: with
        # addition ruled out by the note, the one category left is an update.
        history = build(
            {"v1.17": self._counts("v1.17", 1, 0), "v2.9": self._counts("v2.9", 0, 1)},
            {"v1.17": {}, "v2.9": {}},
            deltas={"v2.9": {"0245cg223": record()}}, totals={"v2.9": 1},
        )
        self.assertEqual(
            [(e["version"], e["event"]) for e in events(history, "0245cg223")],
            [("v2.9", "modified")],
        )
        entry = next(r for r in history["releases"] if r["version"] == "v2.9")
        self.assertEqual(entry["records"], {"added": 0, "updated": 1})
        # The note answered "added" and the whole delta answered the rest.
        self.assertEqual(entry["provenance"],
                         {"added": "release-note", "updated": "delta-comparison"})
        self.assertNotIn("disagreements", entry)

    def test_both_categories_possible_stays_unresolved(self):
        # Same unnamed addition, but now nothing rules either category out: no
        # validated section, and a delta too short to enumerate one.
        history = build(
            {"v1.17": self._counts("v1.17", 1, 0)}, {"v1.17": {}, "v2.9": {}},
            deltas={"v2.9": {"0245cg223": record()}}, totals={"v2.9": 4},
        )
        self.assertEqual(
            [(e["version"], e["event"]) for e in events(history, "0245cg223")],
            [("v2.9", "unresolved")],
        )
        entry = next(r for r in history["releases"] if r["version"] == "v2.9")
        self.assertEqual(entry["records"]["unresolved"], 1)
        # Nothing enumerated a section, so none is claimed.
        self.assertNotIn("provenance", entry)

    def test_a_delta_settles_presence_so_a_later_release_specializes_again(self):
        # v2.9 leaves the category open but proves the record was deployed and
        # hands over its payload; v2.10 can then compare against it.
        history = build(
            {"v1.17": self._counts("v1.17", 1, 0)},
            {"v1.17": {}, "v2.9": {}, "v2.10": {}},
            deltas={
                "v2.9": {"0245cg223": record()},
                "v2.10": {"0245cg223": record("withdrawn", successors=["04ajdc372"])},
            },
            totals={"v2.9": 4, "v2.10": 1},
        )
        self.assertEqual(
            [(e["version"], e["event"]) for e in events(history, "0245cg223")],
            [("v2.10", "withdrawn"), ("v2.9", "unresolved")],
        )
        latest = events(history, "0245cg223")[0]
        self.assertEqual(latest["basis"], "delta-comparison")
        self.assertEqual(latest["successors"], ["04ajdc372"])

    def test_a_note_declared_update_resolves_an_unknown_record(self):
        # The note classifies it outright, so the open membership never gets a
        # say -- and an update it is, with no payload to specialize against.
        note = PREAMBLE.format(version="v2.10", added=0, updated=1) + (
            "\n# **Records updated**\nhttps://ror.org/0245cg223|X\n"
        )
        history = build(
            {"v1.17": self._counts("v1.17", 1, 0),
             "v2.10": RR.parse_note(note, "body")},
            {"v1.17": {}, "v2.10": {}},
        )
        self.assertEqual(
            [(e["version"], e["event"]) for e in events(history, "0245cg223")],
            [("v2.10", "modified")],
        )

    def test_an_additions_only_aggregate_keeps_existing_payloads_fresh(self):
        # It declares "updated: 0", so it cannot have touched a record that
        # already existed -- v2.10 still has a before to compare against.
        history = build(
            {"v1.17": self._counts("v1.17", 3, 0)}, {"v1.17": {}, "v2.10": {}},
            baseline={"042aqky30"},
            deltas={"v2.10": {"042aqky30": record("withdrawn")}}, totals={"v2.10": 1},
        )
        self.assertEqual(
            [(e["version"], e["event"]) for e in events(history, "042aqky30")],
            [("v2.10", "withdrawn"), ("pre-v1.0", "baseline")],
        )

    def test_an_unnamed_update_invalidates_the_payloads_it_could_have_touched(self):
        # Positive "updated" and no listing: the record may have changed into
        # something unrecorded, so v2.10 has no trustworthy before and must not
        # specialize the transition.
        history = build(
            {"v1.17": self._counts("v1.17", 0, 3)}, {"v1.17": {}, "v2.10": {}},
            baseline={"042aqky30"},
            deltas={"v2.10": {"042aqky30": record("withdrawn")}}, totals={"v2.10": 1},
        )
        self.assertEqual(
            [(e["version"], e["event"]) for e in events(history, "042aqky30")],
            [("v2.10", "modified"), ("pre-v1.0", "baseline")],
        )

    def test_a_universal_aggregate_keeps_its_events_and_ages_the_payloads(self):
        # v1.58's shape is untouched: registry-wide for everything present, and
        # every payload stale afterwards, so v2.10 stays generic.
        history = build(
            {"v1.17": RR.parse_note(UNIVERSAL_NOTE, "body")},
            {"v1.17": {}, "v2.10": {}},
            baseline={"042aqky30"},
            deltas={"v2.10": {"042aqky30": record("withdrawn")}}, totals={"v2.10": 1},
        )
        self.assertEqual(
            [(e["version"], e["event"]) for e in events(history, "042aqky30")],
            [("v2.10", "modified"), ("v1.17", "registry-wide"), ("pre-v1.0", "baseline")],
        )
        entry = next(r for r in history["releases"] if r["version"] == "v1.17")
        self.assertEqual(entry["scope"], "universal")
        self.assertEqual(entry["records"], {"present": 1})


class TestSectionInvalidation(unittest.TestCase):
    """Which sections a broken listing disqualifies is a structural fact, not
    something read back out of the diagnostic messages."""

    def _dupe(self, kind, ident):
        return RR.parse_note(
            f"- **Records {kind}**: 2\n# **Records {kind}**\n"
            f"https://ror.org/{ident}|A\nhttps://ror.org/{ident}|A again\n",
            "body",
        )

    def test_the_same_defect_validates_the_same_whatever_it_is_called(self):
        # Different ids put different text in the problem list; the validation
        # outcome is identical because it never consults that text.
        first, second = self._dupe("added", "04ajdc372"), self._dupe("added", "0245cg223")
        self.assertNotEqual(first.problems, second.problems)
        self.assertEqual(first.validated, second.validated)
        self.assertEqual(first.validated, set())

    def test_a_duplicate_disqualifies_only_its_own_section(self):
        text = (
            "- **Records added**: 2\n- **Records updated**: 1\n"
            "# **Records added**\n"
            "https://ror.org/04ajdc372|A\nhttps://ror.org/04ajdc372|A again\n"
            "# **Records updated**\nhttps://ror.org/042aqky30|B\n"
        )
        parsed = RR.parse_note(text, "body")
        self.assertEqual(parsed.validated, {"updated"})

    def test_an_id_in_both_sections_disqualifies_both_despite_matching_counts(self):
        # Each table matches its own declared count, so only the cross-listing
        # itself can rule them out.
        text = (
            "- **Records added**: 1\n- **Records updated**: 1\n"
            "# **Records added**\nhttps://ror.org/04ajdc372|A\n"
            "# **Records updated**\nhttps://ror.org/04ajdc372|A\n"
        )
        parsed = RR.parse_note(text, "body")
        self.assertEqual(parsed.validated, set())

    def test_an_unrelated_heading_still_bounds_the_section_it_closes(self):
        # The rows under a foreign heading are not a listing, so the section
        # validates on the rows that really were under it.
        text = (
            "- **Records added**: 1\n"
            "# **Records added**\nhttps://ror.org/04ajdc372|A\n"
            "# **Deprecated identifiers**\nhttps://ror.org/042aqky30|Not an addition\n"
        )
        parsed = RR.parse_note(text, "body")
        self.assertEqual(parsed.added, ["04ajdc372"])
        self.assertEqual(parsed.validated, {"added"})
        self.assertTrue(parsed.usable)


class TestPresenceAndPayload(unittest.TestCase):
    """Presence decides added; payload freshness decides specialization."""

    def _note(self, version, updated_ids=(), added_ids=()):
        text = PREAMBLE.format(version=version, added=len(added_ids), updated=len(updated_ids))
        for kind, ids in (("added", added_ids), ("updated", updated_ids)):
            if ids:
                text += f"\n# **Records {kind}**\n" + "".join(
                    f"https://ror.org/{i}|x\n" for i in ids
                )
        return RR.parse_note(text, "body")

    def test_a_note_only_update_then_a_delta_specializes_again(self):
        # v1.17 names it with no delta -> payload unknown -> plain modified.
        # v2.9 ships a delta -> payload current again -> the next one can
        # specialize.
        history = build(
            {"v1.17": self._note("v1.17", updated_ids=["042aqky30"]),
             "v2.9": self._note("v2.9", updated_ids=["042aqky30"]),
             "v2.10": self._note("v2.10", updated_ids=["042aqky30"])},
            {"v1.17": {}, "v2.9": {}, "v2.10": {}},
            baseline={"042aqky30"},
            deltas={"v2.9": {"042aqky30": record("active")},
                    "v2.10": {"042aqky30": record("withdrawn")}},
            totals={"v2.9": 1, "v2.10": 1},
        )
        self.assertEqual(
            [(e["version"], e["event"]) for e in events(history, "042aqky30")],
            [("v2.10", "withdrawn"), ("v2.9", "modified"), ("v1.17", "modified"),
             ("pre-v1.0", "baseline")],
        )

    def test_an_unknown_prior_payload_yields_plain_modified(self):
        history = build(
            {"v1.17": self._note("v1.17", updated_ids=["042aqky30"]),
             "v2.9": self._note("v2.9", updated_ids=["042aqky30"])},
            {"v1.17": {}, "v2.9": {}},
            baseline={"042aqky30"},
            deltas={"v2.9": {"042aqky30": record("withdrawn")}}, totals={"v2.9": 1},
        )
        # v1.17 invalidated the payload, so v2.9 cannot claim a withdrawal.
        self.assertEqual([e["event"] for e in events(history, "042aqky30")][0], "modified")

    def test_no_successor_claim_after_unknown_state(self):
        history = build(
            {"v1.17": self._note("v1.17", updated_ids=["042aqky30"]),
             "v2.9": self._note("v2.9", updated_ids=["042aqky30"])},
            {"v1.17": {}, "v2.9": {}},
            baseline={"042aqky30"},
            deltas={"v2.9": {"042aqky30": record("active", successors=["04ajdc372"])}},
            totals={"v2.9": 1},
        )
        self.assertNotIn("successors", events(history, "042aqky30")[0])

    def test_a_note_declared_update_never_becomes_added(self):
        # Present from the GRID era, payload invalidated, then named as updated
        # again by a release whose delta we do not have.
        history = build(
            {"v1.17": self._note("v1.17", updated_ids=["042aqky30"]),
             "v2.9": self._note("v2.9", updated_ids=["042aqky30"])},
            {"v1.17": {}, "v2.9": {}},
            baseline={"042aqky30"},
        )
        self.assertEqual(
            [e["event"] for e in events(history, "042aqky30")],
            ["modified", "modified", "baseline"],
        )

    def test_a_universal_aggregate_invalidates_every_payload(self):
        history = build(
            {"v1.17": RR.parse_note(UNIVERSAL_NOTE, "body"),
             "v2.9": self._note("v2.9", updated_ids=["042aqky30"])},
            {"v1.17": {}, "v2.9": {}},
            baseline={"042aqky30"},
            deltas={"v2.9": {"042aqky30": record("withdrawn")}}, totals={"v2.9": 1},
        )
        self.assertEqual(
            [(e["version"], e["event"]) for e in events(history, "042aqky30")],
            [("v2.9", "modified"), ("v1.17", "registry-wide"), ("pre-v1.0", "baseline")],
        )

    def test_a_non_universal_aggregate_also_invalidates_payloads(self):
        history = build(
            {"v1.17": RR.parse_note(COUNTS_ONLY_NOTE, "body"),
             "v2.9": self._note("v2.9", updated_ids=["042aqky30"])},
            {"v1.17": {}, "v2.9": {}},
            baseline={"042aqky30"},
            deltas={"v2.9": {"042aqky30": record("withdrawn")}}, totals={"v2.9": 1},
        )
        # No event for v1.17 itself, but it could have touched the record, so
        # v2.9 has no trustworthy "before" to specialize against.
        self.assertEqual(
            [(e["version"], e["event"]) for e in events(history, "042aqky30")],
            [("v2.9", "modified"), ("pre-v1.0", "baseline")],
        )

    def test_a_delta_alone_still_specializes(self):
        history = build(
            {}, {"v2.9": {}},
            baseline={"042aqky30"},
            deltas={"v2.9": {"042aqky30": record("withdrawn")}}, totals={"v2.9": 1},
        )
        self.assertEqual([e["event"] for e in events(history, "042aqky30")][0], "withdrawn")


class TestDeltaCompleteness(unittest.TestCase):
    """A delta may only enumerate a section once it is known to be whole."""

    def setUp(self):
        self.short = RR.parse_note(SECTION_SHORT_NOTE, "body")  # added ok, updated short

    def test_a_verified_delta_may_answer_the_short_section(self):
        history = build(
            {"v2.9": self.short}, {"v2.9": {}}, baseline={"042aqky30"},
            deltas={"v2.9": {"014zt3e74": record(), "042aqky30": record()}},
            totals={"v2.9": 5},  # 2 declared added + 3 declared updated
            suffixes={"014zt3e74", "042aqky30"},
        )
        entry = next(r for r in history["releases"] if r["version"] == "v2.9")
        self.assertEqual(entry["provenance"],
                         {"added": "release-note", "updated": "delta-comparison"})
        self.assertTrue(entry["delta_complete"])
        self.assertEqual([e["event"] for e in events(history, "042aqky30")][0], "modified")

    def test_an_unverified_delta_may_not_enumerate_a_section(self):
        # One file short of the declared 2 + 3: absence proves nothing now.
        history = build(
            {"v2.9": self.short}, {"v2.9": {}}, baseline={"042aqky30"},
            deltas={"v2.9": {"014zt3e74": record(), "042aqky30": record()}},
            totals={"v2.9": 4},
            suffixes={"014zt3e74", "042aqky30"},
            transitions={"v2.9": {"042aqky30"}},
        )
        entry = next(r for r in history["releases"] if r["version"] == "v2.9")
        self.assertEqual(entry["provenance"], {"added": "release-note"})
        self.assertFalse(entry["delta_complete"])
        self.assertEqual(entry["status"], "partial")

    def test_a_present_record_file_is_still_evidence(self):
        # Even unverified, the file says this record was deployed, so it gets a
        # real event rather than a notice.
        history = build(
            {"v2.9": self.short}, {"v2.9": {}}, baseline={"042aqky30"},
            deltas={"v2.9": {"014zt3e74": record(), "042aqky30": record()}},
            totals={"v2.9": 4},
            suffixes={"014zt3e74", "042aqky30"},
            transitions={"v2.9": {"042aqky30"}},
        )
        self.assertEqual(
            [(e["version"], e["event"]) for e in events(history, "042aqky30")],
            [("v2.9", "modified"), ("pre-v1.0", "baseline")],
        )
        entry = next(r for r in history["releases"] if r["version"] == "v2.9")
        # And no disagreement is invented to get there. The validated "added"
        # section excludes this record and the truncated "updated" section is
        # exactly where an update would have been listed, so the note and the
        # delta agree; only a leftover sweep would have seen a conflict.
        self.assertNotIn("disagreements", entry)

    def test_a_delta_without_any_declared_counts_cannot_be_verified(self):
        history = build({}, {}, deltas={"v2.9": {}}, totals={"v2.9": 4})
        entry = next(r for r in history["releases"] if r["version"] == "v2.9")
        self.assertFalse(entry.get("delta_complete"))
        self.assertNotIn("provenance", entry)


# --- Section provenance ------------------------------------------------------


class TestSectionProvenance(unittest.TestCase):
    """One record's conflict must not cost either section its provenance.

    v1.17 here lists both sections in full and its counts check out, so both
    *state* their answer. Its delta then ships one record neither section names,
    which no artifact can categorise. That record stays unresolved -- and the two
    validated listings stay authoritative, for the records they name and for the
    ones they omit.
    """

    def setUp(self):
        self.history = build(
            {"v1.17": RR.parse_note(BOTH_SECTIONS_NOTE, "body"),
             # Counts only, so neither section states anything: 014zt3e74's
             # category rests entirely on what is known about its membership.
             "v2.9": RR.parse_note(PREAMBLE.format(version="v2.9", added=1, updated=1), "body"),
             "v2.10": RR.parse_note(PREAMBLE.format(version="v2.10", added=0, updated=1), "body")},
            {"v1.17": {}, "v2.9": {}, "v2.10": {}},
            baseline={"042aqky30", "00z7rqk70"},
            suffixes={"014zt3e74", "042aqky30", "04ajdc372", "0245cg223", "00z7rqk70"},
            deltas={
                "v1.17": {"0245cg223": record(), "00z7rqk70": record(),
                          "04ajdc372": record()},
                "v2.9": {"014zt3e74": record()},
                "v2.10": {"042aqky30": record("withdrawn")},
            },
            # v1.17 declares 1 + 1 and ships 3 files: not a delta that enumerates.
            totals={"v1.17": 3, "v2.9": 2, "v2.10": 1},
        )
        self.v117 = next(r for r in self.history["releases"] if r["version"] == "v1.17")

    def test_both_validated_listings_keep_their_provenance(self):
        self.assertEqual(
            self.v117["provenance"],
            {"added": "release-note", "updated": "release-note"},
        )

    def test_the_conflicting_record_alone_stays_unresolved(self):
        self.assertEqual(self.v117["records"],
                         {"added": 1, "updated": 1, "unresolved": 1, "affected": 0})
        self.assertEqual(
            [(e["version"], e["event"]) for e in events(self.history, "04ajdc372")],
            [("v1.17", "unresolved")],
        )
        self.assertEqual(
            [e["event"] for e in events(self.history, "0245cg223")], ["added"]
        )

    def test_the_release_is_not_called_fully_classified(self):
        # Both provenance keys survived, but a deployed record is still
        # uncategorised -- "classified" would bury exactly that.
        self.assertEqual(self.v117["status"], "partial")
        report = UH.render_report(self.history, {})
        self.assertIn("| `v1.17` | partial | 2 (+1 unresolved) |", report)
        # Both sections *are* answered here, so the summary label may not claim
        # one of them went unanswered.
        self.assertIn("Partly classified (section or record unresolved): 1", report)
        self.assertEqual(self.v117["provenance"].keys(), {"added", "updated"})

    def test_an_unrelated_later_addition_is_still_added(self):
        # The validated "Records added" listing excluded 014zt3e74, so v1.17
        # cannot have added it and its absence survives to v2.9.
        self.assertEqual(
            [(e["version"], e["event"]) for e in events(self.history, "014zt3e74")],
            [("v2.9", "added")],
        )

    def test_an_unrelated_later_withdrawal_is_still_specialized(self):
        # The validated "Records updated" listing excluded 042aqky30, so v1.17
        # cannot have aged its payload and v2.10 has a real before and after.
        self.assertEqual(
            [(e["version"], e["event"]) for e in events(self.history, "042aqky30")],
            [("v2.10", "withdrawn"), ("pre-v1.0", "baseline")],
        )


# --- Local snapshot history --------------------------------------------------


class TestSnapshotWeakening(unittest.TestCase):
    """An unanswered release local history flags weakens state, never sets it.

    v1.17 carries no usable note and no delta. Saxon ROR's own git history says
    it changed the record, which is not enough to classify anything -- but it is
    enough that the next release must not compare against state v1.17 may
    already have moved.
    """

    def _later(self, version, ids, statuses=None):
        """A counts-only "updated: n" note beside a complete delta of ``ids``."""
        statuses = statuses or {}
        return dict(
            attempts={version: RR.parse_note(
                PREAMBLE.format(version=version, added=0, updated=len(ids)), "body"
            )},
            deltas={version: {i: record(statuses.get(i, "active")) for i in ids}},
            totals={version: len(ids)},
        )

    def test_a_possible_addition_makes_prior_absence_unknown(self):
        later = self._later("v2.9", ["014zt3e74"])
        history = build(
            later["attempts"], {"v1.17": {}, "v2.9": {}},
            suffixes={"014zt3e74", "042aqky30"},
            transitions={"v1.17": {"014zt3e74"}},
            deltas=later["deltas"], totals=later["totals"],
        )
        # v1.17 may have added it, so v2.9 can no longer rule an update out.
        self.assertEqual(
            [(e["version"], e["event"]) for e in events(history, "014zt3e74")],
            [("v2.9", "modified"), ("v1.17", "unavailable")],
        )
        entry = next(r for r in history["releases"] if r["version"] == "v2.9")
        self.assertNotIn("disagreements", entry)
        self.assertNotIn("unresolved", entry["records"])

    def test_a_possible_update_makes_the_prior_payload_stale(self):
        later = self._later("v2.9", ["042aqky30"], {"042aqky30": "withdrawn"})
        history = build(
            later["attempts"], {"v1.17": {}, "v2.9": {}},
            baseline={"042aqky30"},
            suffixes={"014zt3e74", "042aqky30"},
            transitions={"v1.17": {"042aqky30"}},
            deltas=later["deltas"], totals=later["totals"],
        )
        # The pre-v1.0 payload is no longer what v1.17 left behind, so v2.9 has
        # no before to compare against and cannot claim a withdrawal.
        self.assertEqual(
            [(e["version"], e["event"]) for e in events(history, "042aqky30")],
            [("v2.9", "modified"), ("v1.17", "unavailable"), ("pre-v1.0", "baseline")],
        )

    def test_weakening_reaches_only_the_records_local_history_names(self):
        # 042aqky30 is not in the transition set, so its payload is untouched
        # and v2.9 still specializes it.
        later = self._later("v2.9", ["042aqky30"], {"042aqky30": "withdrawn"})
        history = build(
            later["attempts"], {"v1.17": {}, "v2.9": {}},
            baseline={"042aqky30"},
            suffixes={"014zt3e74", "042aqky30"},
            transitions={"v1.17": {"014zt3e74"}},
            deltas=later["deltas"], totals=later["totals"],
        )
        self.assertEqual(
            [e["event"] for e in events(history, "042aqky30")],
            ["withdrawn", "baseline"],
        )

    def test_a_weakened_record_gets_no_invented_event(self):
        history = build(
            {}, {"v1.17": {}, "v2.10": {}},
            suffixes={"014zt3e74", "042aqky30"},
            baseline={"042aqky30"},
            transitions={"v1.17": {"014zt3e74", "042aqky30"}},
        )
        kinds = {e["event"] for evs in history["records"].values() for e in evs}
        self.assertTrue(kinds <= {"baseline", "unavailable"}, kinds)


class TestSnapshotConstraints(unittest.TestCase):
    """Local history naming a record does not suspend the official constraints.

    Weakening is the mirror of classifying, so it answers to the same evidence:
    a validated listing that omits the record rules its section out, and what is
    known about membership rules out the other half. Where that leaves no
    category at all, the release cannot have touched the record in any way the
    reconstruction is entitled to act on -- and the snapshot map, which is not an
    official artifact, may not be the thing that decides otherwise.
    """

    TWO = [{"version": "v1.17", "date": "2022-12-15"},
           {"version": "v2.9", "date": "2026-06-23"}]

    def _v29(self, ids, statuses=None, declared=None, files=None):
        """A counts-only v2.9 note beside a delta of ``ids``."""
        statuses = statuses or {}
        added, updated = declared or (0, len(ids))
        return dict(
            attempts={"v2.9": RR.parse_note(
                PREAMBLE.format(version="v2.9", added=added, updated=updated), "body"
            )},
            deltas={"v2.9": {i: record(statuses.get(i, "active")) for i in ids}},
            totals={"v2.9": files if files is not None else len(ids)},
        )

    def _build(self, note, later, **kw):
        attempts = {"v1.17": RR.parse_note(note, "body")}
        attempts.update(later["attempts"])
        return build(
            attempts, {"v1.17": {}, "v2.9": {}}, catalog=self.TWO,
            deltas=later["deltas"], totals=later["totals"], **kw
        )

    def test_a_present_record_the_updated_listing_omits_keeps_its_payload(self):
        # v1.17's validated "Records updated" listing excluded 042aqky30, and it
        # was already present, so v1.17 can have neither added nor updated it --
        # whatever the snapshot map says. Its payload therefore still stands, and
        # v2.9 has a real before to specialize against.
        history = self._build(
            UPDATED_SECTION_ONLY,
            self._v29(["042aqky30"], {"042aqky30": "withdrawn"}),
            baseline={"042aqky30", "0245cg223"},
            suffixes={"042aqky30", "0245cg223"},
            transitions={"v1.17": {"042aqky30"}},
        )
        self.assertEqual(
            [(e["version"], e["event"]) for e in events(history, "042aqky30")],
            [("v2.9", "withdrawn"), ("v1.17", "unavailable"), ("pre-v1.0", "baseline")],
        )

    def test_an_absent_record_the_added_listing_omits_stays_addable(self):
        # The mirror image: v1.17's validated "Records added" listing excluded
        # 014zt3e74, and it was absent, so v1.17 can have done nothing to it. Its
        # absence survives, and v2.9's addition is a real addition.
        history = self._build(
            ADDED_SECTION_ONLY,
            # Counts that do not confirm the delta, so neither section states
            # anything: 014zt3e74's category rests on its membership alone.
            self._v29(["014zt3e74"], declared=(1, 1), files=1),
            suffixes={"014zt3e74", "0245cg223"},
            transitions={"v1.17": {"014zt3e74"}},
        )
        self.assertEqual(
            [(e["version"], e["event"]) for e in events(history, "014zt3e74")],
            [("v2.9", "added"), ("v1.17", "unavailable")],
        )

    def _ruled_out(self):
        """The present-record case, where no category survives at v1.17."""
        return self._build(
            UPDATED_SECTION_ONLY,
            self._v29(["042aqky30"], {"042aqky30": "withdrawn"}),
            baseline={"042aqky30", "0245cg223"},
            suffixes={"042aqky30", "0245cg223"},
            transitions={"v1.17": {"042aqky30"}},
        )

    def test_a_record_no_category_survives_for_still_gets_its_notice(self):
        # Preserving what was known about the record does not mean pretending
        # local history never named it: the release is still one nothing
        # official answered for, and the record still says so.
        history = self._ruled_out()
        entry = next(r for r in history["releases"] if r["version"] == "v1.17")
        self.assertEqual(entry["records"]["affected"], 1)
        self.assertEqual(
            [e["event"] for e in events(history, "042aqky30") if e["version"] == "v1.17"],
            ["unavailable"],
        )

    def test_the_ruled_out_record_is_reported_as_a_disagreement(self):
        history = self._ruled_out()
        entry = next(r for r in history["releases"] if r["version"] == "v1.17")
        self.assertEqual(
            entry["disagreements"],
            ["042aqky30: local snapshot history names it, but the validated "
             "note sections and its membership rule out both categories"],
        )
        self.assertIn(
            "- `v1.17` 042aqky30: local snapshot history names it, but the "
            "validated note sections and its membership rule out both categories",
            UH.render_report(history, {}),
        )

    def test_only_updated_surviving_still_ages_the_payload(self):
        # The validated "Records added" listing omits 042aqky30 and it was
        # present anyway, so an addition is out -- but nothing rules an update
        # out, and v1.17 may well have made one. The payload goes stale, and
        # v2.9 has no before to specialize a withdrawal from.
        history = self._build(
            ADDED_SECTION_NO_UPDATE_COUNT,
            self._v29(["042aqky30"], {"042aqky30": "withdrawn"}),
            baseline={"042aqky30"},
            suffixes={"042aqky30", "0245cg223"},
            transitions={"v1.17": {"042aqky30"}},
        )
        self.assertEqual(
            [(e["version"], e["event"]) for e in events(history, "042aqky30")],
            [("v2.9", "modified"), ("v1.17", "unavailable"), ("pre-v1.0", "baseline")],
        )

    def test_only_added_surviving_still_costs_the_record_its_absence(self):
        # The mirror: the validated "Records updated" listing omits 014zt3e74
        # and it was absent anyway, so an update is out -- but v1.17 may have
        # added it, and v2.9 can no longer call its own delta an addition.
        history = self._build(
            UPDATED_SECTION_NO_ADD_COUNT,
            self._v29(["014zt3e74"], declared=(1, 1), files=1),
            baseline={"0245cg223"},
            suffixes={"014zt3e74", "0245cg223"},
            transitions={"v1.17": {"014zt3e74"}},
        )
        self.assertEqual(
            [(e["version"], e["event"]) for e in events(history, "014zt3e74")],
            [("v2.9", "unresolved"), ("v1.17", "unavailable")],
        )

    def test_the_decision_is_per_record_within_one_release(self):
        # Three records, one release, three answers: 042aqky30 is present and
        # the validated listing omits it, so nothing survives and its payload
        # stands; 014zt3e74 is absent, so an addition survives and its absence
        # does not; 04ajdc372 local history never names, so nothing reaches it.
        history = self._build(
            UPDATED_SECTION_NO_ADD_COUNT,
            self._v29(
                ["042aqky30", "014zt3e74", "04ajdc372"],
                {"042aqky30": "withdrawn", "04ajdc372": "withdrawn"},
                declared=(1, 1), files=3,
            ),
            baseline={"042aqky30", "04ajdc372", "0245cg223"},
            suffixes={"042aqky30", "014zt3e74", "04ajdc372", "0245cg223"},
            transitions={"v1.17": {"042aqky30", "014zt3e74"}},
        )
        latest = {s: events(history, s)[0]["event"]
                  for s in ("042aqky30", "014zt3e74", "04ajdc372")}
        self.assertEqual(
            latest,
            {"042aqky30": "withdrawn", "014zt3e74": "unresolved",
             "04ajdc372": "withdrawn"},
        )
        # Only the record nothing survived for is a disagreement, and the one
        # outside the transition set is not in the release entry at all.
        entry = next(r for r in history["releases"] if r["version"] == "v1.17")
        self.assertEqual(len(entry["disagreements"]), 1)
        self.assertIn("042aqky30", entry["disagreements"][0])
        self.assertEqual(entry["records"]["affected"], 2)


class TestOpenCategories(unittest.TestCase):
    """The single rule classifying and weakening are both decided by.

    A ``None`` listing is a section the note never stated; an empty set is a
    validated listing that omits this record, which is how ``_note_sets`` hands
    one over once it has been narrowed to the tracked subset. The records a
    listing *names* never reach here -- their section states the answer
    outright.
    """

    # (membership, added listing, updated listing) -> what is left open
    CASES = [
        ((UH.UNKNOWN, None, None), {"added", "updated"}),
        ((UH.PRESENT, None, None), {"updated"}),
        ((UH.ABSENT, None, None), {"added"}),
        ((UH.UNKNOWN, set(), None), {"updated"}),
        ((UH.UNKNOWN, None, set()), {"added"}),
        ((UH.PRESENT, set(), None), {"updated"}),
        ((UH.ABSENT, None, set()), {"added"}),
        # Nothing left: each category ruled out by a different kind of evidence.
        ((UH.PRESENT, None, set()), set()),
        ((UH.ABSENT, set(), None), set()),
        ((UH.UNKNOWN, set(), set()), set()),
        ((UH.PRESENT, set(), set()), set()),
    ]

    def test_every_combination_of_constraints(self):
        for (was, note_added, note_updated), expected in self.CASES:
            with self.subTest(membership=was, added=note_added, updated=note_updated):
                self.assertEqual(
                    UH._open_categories(was, note_added, note_updated), expected
                )


# --- Aggregate-only ----------------------------------------------------------


class TestAggregateOnlyNeedsNoDelta(unittest.TestCase):
    """A release ROR shipped a delta for is not one it described in the whole."""

    def _history(self):
        return build(
            # v1.17 is the genuine article: counts, no listing, no delta. It
            # leaves 04ajdc372's membership open, which is what makes v2.9's
            # delta record uncategorisable.
            {"v1.17": RR.parse_note(PREAMBLE.format(version="v1.17", added=3, updated=0), "body"),
             "v2.9": RR.parse_note(PREAMBLE.format(version="v2.9", added=5, updated=7), "body")},
            {"v1.17": {}, "v2.9": {}},
            suffixes={"04ajdc372"},
            catalog=[{"version": "v1.17", "date": "2022-12-15"},
                     {"version": "v2.9", "date": "2026-06-23"}],
            deltas={"v2.9": {"04ajdc372": record()}},
            totals={"v2.9": 3},
        )

    def test_a_delta_disqualifies_the_aggregate_only_verdict(self):
        history = self._history()
        entry = next(r for r in history["releases"] if r["version"] == "v2.9")
        self.assertNotEqual(entry["status"], "aggregate-only")
        self.assertEqual(entry["status"], "pending")  # it is the newest release
        self.assertEqual(entry["records"]["unresolved"], 1)
        self.assertEqual(
            [(e["version"], e["event"]) for e in events(history, "04ajdc372")],
            [("v2.9", "unresolved")],
        )

    def test_it_is_reported_and_warned_about(self):
        history = self._history()
        report = UH.render_report(history, {})
        self.assertIn("Aggregate-only (counts published, no per-record list): 1", report)
        self.assertIn("| `v2.9` | pending |", report)
        problems = UH._warning_problems(history, {}, {})
        self.assertEqual(len(problems), 1)
        self.assertIn("`v2.9`", problems[0])

    def test_the_genuine_aggregate_only_release_is_untouched(self):
        entry = next(r for r in self._history()["releases"] if r["version"] == "v1.17")
        self.assertEqual(entry["status"], "aggregate-only")
        self.assertEqual(entry["declared"], {"added": 3, "updated": 0})
        self.assertNotIn("delta_files", entry)


# --- Warnings ----------------------------------------------------------------


class TestWarnings(unittest.TestCase):
    def setUp(self):
        self.v210 = RR.parse_note(INLINE_NOTE, "body")

    def test_pending_release_warns(self):
        history = build({}, {"v2.10": {}}, transitions={"v2.10": {"042aqky30"}})
        attempts = {"v2.10": RR.parse_note(TRUNCATED_BODY, "body")}
        problems = UH._warning_problems(history, attempts, {})
        self.assertEqual(len(problems), 1)
        self.assertIn("v2.10", problems[0])

    def test_long_standing_unavailable_releases_do_not_warn_every_run(self):
        history = build({"v2.10": self.v210}, {"v2.9": {}, "v2.10": {}})
        self.assertEqual(UH._warning_problems(history, {}, {}), [])

    def test_a_release_that_used_to_classify_warns(self):
        history = build({"v2.10": self.v210}, {"v2.9": {}, "v2.10": {}})
        previous = {"releases": [{"version": "v2.9", "status": "classified"}]}
        problems = UH._warning_problems(history, {}, previous)
        self.assertEqual(len(problems), 1)
        self.assertIn("was classified, now unavailable", problems[0])

    def test_an_aggregate_only_release_turning_unresolved_warns(self):
        # No per-record event either way, so only the status move reveals it.
        history = build({"v2.10": self.v210}, {"v2.9": {}, "v2.10": {}})
        previous = {"releases": [{"version": "v2.9", "status": "aggregate-only"}]}
        problems = UH._warning_problems(history, {}, previous)
        self.assertEqual(len(problems), 1)
        self.assertIn("was aggregate-only, now unavailable", problems[0])

    def test_a_partial_release_losing_a_section_warns(self):
        history = build({"v2.10": self.v210}, {"v2.9": {}, "v2.10": {}})
        previous = {
            "releases": [
                {"version": "v2.9", "status": "partial",
                 "provenance": {"added": "release-note"}}
            ]
        }
        problems = UH._warning_problems(history, {}, previous)
        self.assertIn("no longer validates: added", problems[0])

    def test_a_section_can_disappear_without_any_saxon_event_being_lost(self):
        # v2.10 keeps its Saxon events but stops answering for "updated". No
        # event vanishes, so only the section comparison can catch it.
        note = PREAMBLE.format(version="v2.10", added=2, updated=1) + (
            "\n# **Records added**\n"
            "https://ror.org/04ajdc372|A\nhttps://ror.org/00z7rqk70|B\n"
        )
        history = build({"v2.10": RR.parse_note(note, "body")}, {"v2.10": {}})
        previous = json.loads(json.dumps(history))
        previous["releases"][-1]["provenance"] = {
            "added": "release-note", "updated": "release-note"
        }
        self.assertEqual(UH.count_events(history), UH.count_events(previous))
        problems = UH._warning_problems(history, {}, previous)
        self.assertEqual(len(problems), 1)
        self.assertIn("no longer validates: updated", problems[0])

    def test_schema_1_validated_lists_are_still_comparable(self):
        history = build({"v2.10": self.v210}, {"v2.10": {}})
        previous = {"releases": [{"version": "v2.10", "status": "classified",
                                  "validated": ["added", "updated"]}]}
        self.assertEqual(UH._warning_problems(history, {}, previous), [])
        shrunk = {"releases": [{"version": "v2.10", "status": "classified",
                                "validated": ["added", "updated", "removed"]}]}
        self.assertIn("no longer validates: removed",
                      UH._warning_problems(history, {}, shrunk)[0])

    def _downgraded(self, **provenance):
        """``(history, previous)`` differing only in where a section came from.

        The shape a run against unreachable release notes leaves behind: same
        keys, same status, same events, a weaker artifact underneath.
        """
        history = build({"v2.10": self.v210}, {"v2.10": {}})
        previous = json.loads(json.dumps(history))
        previous["releases"][-1]["provenance"] = {
            "added": "release-note", "updated": "release-note"
        }
        history["releases"][-1]["provenance"].update(provenance)
        return history, previous

    def test_a_section_falling_back_to_the_delta_warns(self):
        history, previous = self._downgraded(updated="delta-comparison")
        # Everything the other guards look at is unchanged.
        self.assertEqual(UH._sections(history["releases"][-1]),
                         UH._sections(previous["releases"][-1]))
        self.assertEqual(history["releases"][-1]["status"], "classified")
        self.assertEqual(UH.count_events(history), UH.count_events(previous))
        problems = UH._warning_problems(history, {}, previous)
        self.assertEqual(
            problems,
            ["`v2.10`: updated provenance weakened from release-note to delta-comparison"],
        )

    def test_both_sections_downgrading_stay_one_release_entry(self):
        history, previous = self._downgraded(
            added="delta-comparison", updated="delta-comparison"
        )
        problems = UH._warning_problems(history, {}, previous)
        self.assertEqual(len(problems), 1)
        self.assertEqual(
            problems[0],
            "`v2.10`: added provenance weakened from release-note to delta-comparison; "
            "updated provenance weakened from release-note to delta-comparison",
        )

    def test_gaining_a_release_note_is_not_a_downgrade(self):
        history, previous = self._downgraded()
        previous["releases"][-1]["provenance"] = {
            "added": "delta-comparison", "updated": "delta-comparison"
        }
        self.assertEqual(UH._warning_problems(history, {}, previous), [])

    def test_an_unchanged_provenance_map_is_quiet(self):
        history, previous = self._downgraded()
        self.assertEqual(UH._warning_problems(history, {}, previous), [])

    def test_a_schema_1_previous_entry_reports_no_downgrade(self):
        # "validated" names sections and no sources, so the upgrade to a
        # provenance map must not read as every section losing its footing.
        history = build({"v2.10": self.v210}, {"v2.10": {}})
        previous = json.loads(json.dumps(history))
        entry = previous["releases"][-1]
        entry["validated"] = sorted(entry.pop("provenance"))
        self.assertEqual(UH._warning_problems(history, {}, previous), [])

    def test_an_unrecognised_source_is_reported_without_a_direction(self):
        history, previous = self._downgraded(updated="some-future-artifact")
        problems = UH._warning_problems(history, {}, previous)
        self.assertEqual(
            problems,
            ["`v2.10`: updated provenance changed from release-note "
             "to some-future-artifact (unranked source)"],
        )

    def test_moving_off_an_unrecognised_source_is_not_called_a_weakening(self):
        # Nothing here ranks the old source, so neither direction may be
        # claimed -- but the change still has to be visible.
        history, previous = self._downgraded()
        previous["releases"][-1]["provenance"]["updated"] = "some-future-artifact"
        problems = UH._warning_problems(history, {}, previous)
        self.assertEqual(
            problems,
            ["`v2.10`: updated provenance changed from some-future-artifact "
             "to release-note (unranked source)"],
        )
        self.assertNotIn("weakened", problems[0])

    def test_a_shrinking_record_delta_warns(self):
        history = build({}, {"v2.10": {}}, deltas={"v2.10": {}}, totals={"v2.10": 40})
        previous = json.loads(json.dumps(history))
        previous["releases"][-1]["delta_files"] = 900
        problems = UH._warning_problems(history, {}, previous)
        self.assertIn("record delta shrank from 900 to 40 files", problems[0])

    def test_a_delta_that_disappeared_entirely_warns(self):
        # No delta_files key at all now: gone, not unchanged.
        history = build({}, {"v2.10": {}})
        previous = json.loads(json.dumps(history))
        previous["releases"][-1]["delta_files"] = 900
        problems = UH._warning_problems(history, {}, previous)
        self.assertEqual(len(problems), 1)
        self.assertIn("record delta shrank from 900 to 0 files", problems[0])

    def test_report_counts_use_the_schema_2_key(self):
        history = build({"v2.10": self.v210}, {"v2.10": {}})
        entry = next(r for r in history["releases"] if r["version"] == "v2.10")
        entry["status"] = "partial"  # force it into the report table
        entry["records"] = {"added": 0, "updated": 2, "affected": 0}
        self.assertIn("| 2 |", UH.render_report(history, {}))

    def test_report_counts_fall_back_to_the_schema_1_key(self):
        history = build({"v2.10": self.v210}, {"v2.10": {}})
        entry = next(r for r in history["releases"] if r["version"] == "v2.10")
        entry["status"] = "partial"
        entry["records"] = {"added": 1, "modified": 3}
        self.assertIn("| 4 |", UH.render_report(history, {}))

    def test_disappearing_classified_events_warn_even_when_the_status_holds(self):
        # Same status both runs, but a record's event is gone.
        history = build({"v2.10": self.v210}, {"v2.10": {}})
        previous = json.loads(json.dumps(history))
        previous["records"]["0245cg223"] = [
            {"version": "v2.10", "event": "modified", "date": "2026-07-20"}
        ]
        problems = UH._warning_problems(history, {}, previous)
        self.assertEqual(len(problems), 1)
        self.assertIn("1 classified event(s) disappeared", problems[0])

    def test_a_stable_run_warns_about_nothing(self):
        history = build({"v2.10": self.v210}, {"v2.10": {}})
        self.assertEqual(UH._warning_problems(history, {}, history), [])

    def test_classified_entries_record_where_each_section_came_from(self):
        history = build({"v2.10": self.v210}, {"v2.10": {}})
        entry = next(r for r in history["releases"] if r["version"] == "v2.10")
        self.assertEqual(entry["status"], "classified")
        self.assertEqual(entry["provenance"], {"added": "release-note", "updated": "release-note"})

    def test_aggregate_only_release_does_not_warn(self):
        history = build(
            {"v2.9": RR.parse_note(COUNTS_ONLY_NOTE, "body"), "v2.10": self.v210},
            {"v2.9": {}, "v2.10": {}},
        )
        self.assertEqual(UH._warning_problems(history, {}, {}), [])

    def test_report_lists_unresolved_versions(self):
        history = build({"v2.10": self.v210}, {"v2.9": {}, "v2.10": {}})
        report = UH.render_report(history, {})
        self.assertIn("`v2.9`", report)
        self.assertIn("unavailable", report)

    def test_report_separates_aggregate_only_from_unresolved(self):
        history = build(
            {"v2.9": RR.parse_note(COUNTS_ONLY_NOTE, "body"), "v2.10": self.v210},
            {"v1.17": {}, "v2.9": {}, "v2.10": {}},
            transitions={"v1.17": {"042aqky30"}},
        )
        report = UH.render_report(history, {})
        self.assertIn("Aggregate-only (counts published, no per-record list): 1", report)
        self.assertIn("- Unresolved: 3", report)
        self.assertIn("no per-record events by design: `v2.9`", report)

    def test_report_counts_events_and_notices_separately(self):
        history = build(
            {"v2.10": self.v210},
            {"v1.17": {}, "v2.10": {}},
            transitions={"v1.17": {"042aqky30"}},
        )
        # Two classified events (added + modified) and one unresolved notice.
        self.assertEqual(UH.count_events(history), (2, 1))
        report = UH.render_report(history, {})
        self.assertIn("Classified release events across 3 records: 2", report)
        self.assertIn("Unresolved classification notices: 1", report)


# --- Writing -----------------------------------------------------------------


class TestAtomicWrite(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "history.json"
        self.addCleanup(self.tmp.cleanup)

    def test_write_then_reread(self):
        R.dump_json_atomic({"a": [1, 2]}, self.path)
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8")), {"a": [1, 2]})
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])

    def test_a_shrinking_zenodo_inventory_refuses_to_write(self):
        # A truncated or empty Zenodo listing looks exactly like a registry that
        # lost releases. Rewriting from it would drop their events silently --
        # a release that is absent cannot be reported as having regressed.
        previous = build({"v2.10": RR.parse_note(INLINE_NOTE, "body")}, {"v2.10": {}})
        R.dump_json_atomic(previous, self.path)
        intact = self.path.read_bytes()

        for inventory in ([], [v for v in CATALOG if v["version"] != "v2.9"]):
            with self.assertRaises(RuntimeError) as caught:
                UH.write_overlay({"records": {}}, self.path, previous, inventory)
            self.assertIn("refusing to rewrite", str(caught.exception))
            self.assertEqual(self.path.read_bytes(), intact)
            self.assertEqual(list(self.path.parent.iterdir()), [self.path])

    def test_a_complete_inventory_writes_normally(self):
        previous = build({"v2.10": RR.parse_note(INLINE_NOTE, "body")}, {"v2.10": {}})
        R.dump_json_atomic(previous, self.path)
        UH.write_overlay({"records": {"x": []}}, self.path, previous, CATALOG)
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8")), {"records": {"x": []}})

    def test_inventory_checks_report_what_went_missing(self):
        previous = build({"v2.10": RR.parse_note(INLINE_NOTE, "body")}, {"v2.10": {}})
        self.assertEqual(UH.inventory_problems(previous, CATALOG), [])
        self.assertIn("came back empty", UH.inventory_problems(previous, [])[0])
        short = [v for v in CATALOG if v["version"] not in ("v1.17", "v2.9")]
        self.assertIn("v1.17, v2.9", UH.inventory_problems(previous, short)[0])

    def test_a_first_run_has_no_inventory_to_compare_against(self):
        self.assertEqual(UH.inventory_problems({}, CATALOG), [])

    def test_a_failed_write_leaves_the_previous_file_intact(self):
        R.dump_json_atomic({"keep": True}, self.path)

        class Boom:
            pass

        with self.assertRaises(TypeError):
            R.dump_json_atomic({"bad": Boom()}, self.path)
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8")), {"keep": True})
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])


# --- Committed overlay -------------------------------------------------------


class TestCommittedOverlay(unittest.TestCase):
    """Checks the generated data/history.json actually in the repository."""

    @classmethod
    def setUpClass(cls):
        path = R.HISTORY_PATH
        if not path.exists():
            raise unittest.SkipTest("data/history.json has not been generated yet")
        cls.history = json.loads(path.read_text(encoding="utf-8"))
        records = json.loads((R.DATA_DIR / "records.json").read_text(encoding="utf-8"))
        cls.suffixes = {R.ror_suffix(rec) for rec in records}

    def test_keys_match_the_current_records(self):
        self.assertEqual(set(self.history["records"]), self.suffixes)

    def test_stiftung_hochschulmedizin_was_added_in_v2_10(self):
        entries = self.history["records"]["04ajdc372"]
        added = [e for e in entries if e["event"] == "added"]
        self.assertEqual(len(added), 1)
        self.assertEqual(added[0]["version"], "v2.10")
        self.assertEqual(added[0]["date"], "2026-07-20")
        self.assertEqual(
            added[0]["url"], "https://github.com/ror-community/ror-updates/releases/tag/v2.10"
        )

    def test_no_event_is_fabricated_for_v1_17_1(self):
        # It publishes a record delta but no release note, so nothing
        # corroborates the delta's size and it may not enumerate a section.
        # Either way it touches no Saxon record and invents nothing.
        entry = next(r for r in self.history["releases"] if r["version"] == "v1.17.1")
        self.assertEqual(entry["records"], {"affected": 0})
        self.assertFalse(entry["delta_complete"])
        for entries in self.history["records"].values():
            self.assertNotIn("v1.17.1", [e["version"] for e in entries])

    def test_the_baseline_is_the_grid_era_pseudo_release(self):
        entry = self.history["releases"][0]
        self.assertEqual(entry["version"], "pre-v1.0")
        self.assertEqual(entry["date"], "2021-09-23")
        baselines = [
            e for v in self.history["records"].values() for e in v
            if e["event"] == "baseline"
        ]
        self.assertTrue(baselines)
        for event in baselines:
            self.assertEqual(event["version"], "pre-v1.0")
            self.assertNotIn("url", event)

    def test_every_event_is_well_formed(self):
        allowed = set(UH.CLASSIFIED_EVENTS) | {UH.PENDING, UH.UNAVAILABLE, UH.UNRESOLVED}
        dates = {r["version"]: r["date"] for r in self.history["releases"]}
        for suffix, entries in self.history["records"].items():
            versions = [e["version"] for e in entries]
            self.assertEqual(
                versions, sorted(versions, key=UH.version_key, reverse=True), suffix
            )
            for event in entries:
                self.assertIn(event["event"], allowed)
                self.assertEqual(event["date"], dates[event["version"]])
                if "url" in event:
                    self.assertEqual(event["url"], RR.release_url(event["version"]))
                for successor in event.get("successors", []):
                    self.assertRegex(successor, r"^0[0-9a-z]{8}$")

    def test_no_credential_leaked_into_the_overlay(self):
        blob = json.dumps(self.history)
        for needle in ("Authorization", "ghp_", "github_pat_", "Bearer "):
            self.assertNotIn(needle, blob)


if __name__ == "__main__":
    unittest.main()
