"""Network-free tests for the re3data companion layer.

Fixtures are small XML documents in the shape of the re3data schema 4.0 export
(one file per repository, zipped), built in a temporary directory per test.
"""

from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
import zipfile
from contextlib import redirect_stderr
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import ror_lib as R  # noqa: E402
import update_re3data as U  # noqa: E402

TUD = "https://ror.org/042aqky30"
TUBAF = "https://ror.org/031vc2293"
SLUB = "https://ror.org/03wf51b65"
NOT_SAXON = "https://ror.org/02nr0ka47"  # valid checksum, not in the fixture subset


def institution(name, roles=(), ror=None, ident_type="ROR", start=None, end=None, extra_ids=()):
    parts = [f'<r3d:institutionName language="deu">{name}</r3d:institutionName>']
    parts += ["<r3d:institutionCountry>DEU</r3d:institutionCountry>"]
    parts += [f"<r3d:responsibilityType>{r}</r3d:responsibilityType>" for r in roles]
    parts += ["<r3d:institutionType>non-profit</r3d:institutionType>"]
    rors = [ror] if isinstance(ror, str) else list(ror or [])
    for value in rors:
        parts.append(
            "<r3d:institutionIdentifier>"
            f"<r3d:institutionIdentifierType>{ident_type}</r3d:institutionIdentifierType>"
            f"<r3d:institutionIdentifierUrl>{value}</r3d:institutionIdentifierUrl>"
            "</r3d:institutionIdentifier>"
        )
    for itype, value in extra_ids:
        parts.append(
            "<r3d:institutionIdentifier>"
            f"<r3d:institutionIdentifierType>{itype}</r3d:institutionIdentifierType>"
            f"<r3d:institutionIdentifierUrl>{value}</r3d:institutionIdentifierUrl>"
            "</r3d:institutionIdentifier>"
        )
    if start:
        parts.append(f"<r3d:responsibilityStartDate>{start}</r3d:responsibilityStartDate>")
    if end:
        parts.append(f"<r3d:responsibilityEndDate>{end}</r3d:responsibilityEndDate>")
    return "<r3d:institution>" + "".join(parts) + "</r3d:institution>"


def repository_xml(rid, name, institutions=(), url="https://example.org/", types=(),
                   start=None, closed=None, offline=None, namespace=U.SCHEMA_NAMESPACE,
                   last_update="2024-12-05"):
    end = ""
    if closed or offline:
        end = "<r3d:endDate>"
        if closed:
            end += f"<r3d:closed>{closed}</r3d:closed>"
        if offline:
            end += f"<r3d:offline>{offline}</r3d:offline>"
        end += "</r3d:endDate>"
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        f'<r3d:re3data xmlns:r3d="{namespace}"><r3d:repository>'
        f"<r3d:identifiers><r3d:re3data>{rid}</r3d:re3data>"
        "<r3d:doi>https://doi.org/10.17616/R3TEST</r3d:doi></r3d:identifiers>"
        f'<r3d:repositoryName language="eng">{name}</r3d:repositoryName>'
        f"<r3d:repositoryUrl>{url}</r3d:repositoryUrl>"
        + "".join(f"<r3d:type>{t}</r3d:type>" for t in types)
        + (f"<r3d:startDate>{start}</r3d:startDate>" if start else "")
        + end
        + "".join(institutions)
        + "<r3d:entryDate>2019-03-07</r3d:entryDate>"
        f"<r3d:lastUpdate>{last_update}</r3d:lastUpdate>"
        "</r3d:repository></r3d:re3data>"
    )


# The OPARA shape: a technical unit without ROR before and after the entry
# that names TU Dresden by ROR with a general responsibility.
OPARA = repository_xml(
    "r3d100012966", "OPARA",
    [
        institution("TU Bergakademie Freiberg, Universitätsrechenzentrum", ["technical"]),
        institution("Technische Universität Dresden", ["general"], TUD,
                    extra_ids=[("RRID", "https://scicrunch.org/resolver/RRID:SCR_004799")]),
        institution("TU Dresden, Zentrum für Informationsdienste und Hochleistungsrechnen",
                    ["technical"]),
    ],
    url="https://opara.zih.tu-dresden.de/", types=["institutional", "multidisciplinary"],
    start="2018-02-07",
)


class Fixture(unittest.TestCase):
    """A temporary data/ directory with a three-record ROR subset."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.data = self.root / "data"
        records = [{"id": TUD}, {"id": TUBAF}, {"id": SLUB}]
        R.dump_json(records, self.data / "records.json")
        for rec in records:
            R.dump_json(rec, self.data / "records" / f"{R.ror_suffix(rec)}.json")
        self.meta = {
            "ror": {"dump_version": "v2.15", "record_count": 3},
            "reuse": {"openalex": {"matched": 3, "missing_ror_ids": []}},
        }
        R.write_meta(self.meta, self.data)
        # The fixtures are tiny; lift the real-export sanity floors.
        for name, value in (("MIN_EXPORT_REPOSITORIES", 1), ("MIN_EXPORT_ROR_IDENTIFIERS", 1)):
            patcher = mock.patch.object(U, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def make_zip(self, docs: dict[str, str | bytes], name="export.zip") -> Path:
        path = self.root / name
        with zipfile.ZipFile(path, "w") as zf:
            for member, content in docs.items():
                zf.writestr(member, content)
        return path

    def run_update(self, docs, **kw):
        return U.update(self.make_zip(docs), self.data, retrieved="2026-10-27", method="local-file", **kw)

    def layer(self):
        return self.data / "reuse" / "re3data"

    def record(self, ror):
        return json.loads((self.layer() / "records" / f"{R._suffix(ror)}.json").read_text())

    def snapshot(self):
        return {
            str(p.relative_to(self.data)): p.read_bytes()
            for p in sorted(self.data.rglob("*")) if p.is_file()
        }


class NormalizeRorTests(unittest.TestCase):
    def test_canonical_and_accepted_spellings(self):
        for value in [
            "https://ror.org/042aqky30",
            " https://ror.org/042aqky30 ",
            "http://ror.org/042aqky30",
            "https://www.ror.org/042aqky30",
            "https://ROR.org/042aqky30/",
            "ror.org/042aqky30",
            "042aqky30",
            "https://ror.org/ror.org/042aqky30",  # attested in the export
            "https://ror.org/ROR:042aqky30",       # attested in the export
            "https://ror.org/ 042aqky30",          # attested in the export
        ]:
            with self.subTest(value=value):
                self.assertEqual(U.normalize_ror(value), TUD)

    def test_rejected_values(self):
        for value in [
            None, "", "https://ror.org/",
            "https://ror.org/042aqky31",          # checksum mismatch
            "https://ror.org/042AQKY30",          # ids are lowercase
            "https://ror.org/042aqky3",           # too short
            "https://ror.org/142aqky30",          # must start with 0
            "https://ror.org/org/042aqky30",      # truncated prefix
            "https://ror.org/042aqky30;",         # trailing punctuation
            "https://ror.org/SCR_011337",         # another identifier under ror.org
            "https://example.org/042aqky30",
            "https://ror.org/042aqky30/extra",
            "https://grid.ac/institutes/grid.4488.0",
        ]:
            with self.subTest(value=value):
                self.assertIsNone(U.normalize_ror(value))

    def test_checksum_on_whole_local_subset(self):
        for rec in json.loads((REPO_ROOT / "data/records.json").read_text()):
            self.assertEqual(U.normalize_ror(rec["id"]), rec["id"])


class ParseTests(unittest.TestCase):
    def test_opara_roles_stay_on_their_entry(self):
        repo = U.parse_repository(OPARA.encode(), "100012966.xml")
        self.assertEqual(repo["id"], "r3d100012966")
        insts = repo["institutions"]
        self.assertEqual([i["position"] for i in insts], [1, 2, 3])
        self.assertEqual([i["ror_ids"] for i in insts], [[], [TUD], []])
        self.assertEqual(insts[1]["responsibility_types"], ["general"])
        self.assertEqual(insts[2]["responsibility_types"], ["technical"])

    def test_optional_fields_absent(self):
        xml = repository_xml("r3d100000001", "Minimal", [institution("X")], url="")
        repo = U.parse_repository(xml.encode(), "m.xml")
        self.assertIsNone(repo["url"])
        self.assertIsNone(repo["start_date"])
        self.assertIsNone(repo["closed_date"])
        self.assertIsNone(repo["offline_date"])
        self.assertEqual(repo["types"], [])
        self.assertEqual(repo["institutions"][0]["responsibility_types"], [])

    def test_only_ror_typed_identifiers_count(self):
        xml = repository_xml("r3d100000002", "Other types", [
            institution("A", ["general"], ror=f"other:{TUD}", ident_type="other"),
            institution("B", ["general"], ror=TUD, ident_type="GRID"),
            institution("C", ["general"], ror=TUD, ident_type=" ror "),
        ])
        repo = U.parse_repository(xml.encode(), "o.xml")
        self.assertEqual([i["ror_ids"] for i in repo["institutions"]], [[], [], [TUD]])

    def test_unknown_namespace_is_an_error(self):
        xml = repository_xml("r3d100000003", "Future", namespace="http://www.re3data.org/schema/5-0")
        with self.assertRaisesRegex(U.ExportError, "namespace"):
            U.parse_repository(xml.encode(), "f.xml")

    def test_invalid_xml_is_an_error(self):
        with self.assertRaisesRegex(U.ExportError, "invalid XML"):
            U.parse_repository(OPARA.encode()[:-40], "broken.xml")

    def test_missing_id_is_an_error(self):
        xml = OPARA.replace("<r3d:re3data>r3d100012966</r3d:re3data>", "")
        with self.assertRaisesRegex(U.ExportError, "re3data id"):
            U.parse_repository(xml.encode(), "noid.xml")

    def test_doctype_is_rejected(self):
        xml = OPARA.replace("?>\n", '?>\n<!DOCTYPE r3d:re3data [<!ENTITY x "y">]>', 1)
        with self.assertRaisesRegex(U.ExportError, "DOCTYPE"):
            U.parse_repository(xml.encode(), "dtd.xml")


class MatchTests(Fixture):
    def test_opara_end_to_end(self):
        block = self.run_update({"100012966.xml": OPARA})
        rec = self.record(TUD)
        [repo] = rec["repositories"]
        self.assertEqual(repo["re3data_url"], "https://www.re3data.org/repository/r3d100012966")
        self.assertEqual(repo["url"], "https://opara.zih.tu-dresden.de/")
        self.assertEqual(repo["types"], ["institutional", "multidisciplinary"])
        self.assertEqual(repo["start_date"], "2018-02-07")
        self.assertEqual(repo["last_update"], "2024-12-05")
        [inst] = repo["matched_institutions"]
        self.assertEqual(inst["position"], 2)
        self.assertEqual(inst["responsibility_types"], ["general"])
        self.assertEqual(inst["name"], "Technische Universität Dresden")
        # TU Bergakademie Freiberg is in the subset, but its entry carries no
        # ROR id -- a name alone never matches.
        self.assertEqual(block["missing_ror_ids"], [TUBAF, SLUB])
        # Source XML kept verbatim, named by the validated id.
        self.assertEqual((self.layer() / "sources/r3d100012966.xml").read_bytes(), OPARA.encode())

    def test_name_mention_without_ror_does_not_match(self):
        xml = repository_xml("r3d100000010", "Named only", [
            institution("Sächsische Landesbibliothek – Staats- und Universitätsbibliothek Dresden",
                        ["general"], extra_ids=[("Wikidata", "https://www.wikidata.org/wiki/Q323270")]),
        ], url="https://slub-dresden.de/")
        block = self.run_update({"a.xml": OPARA, "b.xml": xml})
        self.assertNotIn(SLUB, block["matched_ror_ids"])

    def test_many_to_many(self):
        shared = repository_xml("r3d100000020", "Shared", [
            institution("TU Dresden", ["funding"], TUD),
            institution("TU Bergakademie Freiberg", ["general"], TUBAF),
            institution("Elsewhere", ["technical"], NOT_SAXON),
        ])
        block = self.run_update({"a.xml": OPARA, "b.xml": shared})
        self.assertEqual(
            [r["re3data_id"] for r in self.record(TUD)["repositories"]],
            ["r3d100000020", "r3d100012966"],
        )
        [freiberg] = self.record(TUBAF)["repositories"]
        self.assertEqual(freiberg["matched_institutions"][0]["responsibility_types"], ["general"])
        shared_tud = self.record(TUD)["repositories"][0]
        self.assertEqual(shared_tud["matched_institutions"][0]["responsibility_types"], ["funding"])
        self.assertEqual(
            (block["matched"], block["repositories"], block["relationships"]), (2, 2, 3)
        )
        self.assertEqual(sorted(p.name for p in (self.layer() / "sources").iterdir()),
                         ["r3d100000020.xml", "r3d100012966.xml"])

    def test_dedupe_keeps_every_matching_entry(self):
        xml = repository_xml("r3d100000030", "Twice", [
            institution("TU Dresden", ["general"], TUD, start="2010", end="2015"),
            institution("Neighbour", ["technical"]),
            institution("Technische Universität Dresden", ["funding", "sponsoring"],
                        [TUD, "https://ror.org/042aqky30/"], start="2016"),
        ])
        block = self.run_update({"x.xml": xml})
        [repo] = self.record(TUD)["repositories"]
        entries = repo["matched_institutions"]
        self.assertEqual([e["position"] for e in entries], [1, 3])
        self.assertEqual(entries[0]["responsibility_types"], ["general"])
        self.assertEqual(entries[0]["responsibility_end_date"], "2015")
        # An unknown role is kept as given; the website labels it generically.
        self.assertEqual(entries[1]["responsibility_types"], ["funding", "sponsoring"])
        self.assertEqual(entries[1]["responsibility_start_date"], "2016")
        self.assertIsNone(entries[1]["responsibility_end_date"])
        self.assertEqual((block["relationships"], block["matched_institution_entries"]), (1, 2))

    def test_status_dates_are_kept(self):
        xml = repository_xml("r3d100000040", "Gone", [institution("TU Dresden", ["general"], TUD)],
                             closed="2019", offline="2022-05-01", start="2010")
        self.run_update({"x.xml": xml})
        [repo] = self.record(TUD)["repositories"]
        self.assertEqual((repo["start_date"], repo["closed_date"], repo["offline_date"]),
                         ("2010", "2019", "2022-05-01"))

    def test_rejected_and_normalized_identifiers_reported(self):
        xml = repository_xml("r3d100000050", "Odd", [
            institution("A", ["general"], "https://ror.org/ror.org/042aqky30"),
            institution("B", ["general"], "https://ror.org/031vc2293;"),
        ])
        block = self.run_update({"x.xml": xml})
        self.assertEqual(block["matched_ror_ids"], [TUD])
        self.assertEqual(block["normalized_ror_identifiers"], 1)
        self.assertEqual(block["rejected_ror_identifiers"],
                         [{"re3data_id": "r3d100000050", "value": "https://ror.org/031vc2293;"}])


class PublishTests(Fixture):
    def test_meta_counters_and_foreign_blocks(self):
        block = self.run_update({"a.xml": OPARA})
        meta = R.read_meta(self.data)
        self.assertEqual(meta["ror"], self.meta["ror"])
        self.assertEqual(meta["reuse"]["openalex"], self.meta["reuse"]["openalex"])
        self.assertEqual(meta["reuse"]["re3data"], block)
        for key, value in {
            "ror_total": 3, "matched": 1, "missing": 2, "repositories": 1,
            "relationships": 1, "retrieved": "2026-10-27", "license": "CC0-1.0",
            "schema_version": "4.0", "export_repositories": 1,
            "latest_matched_last_update": "2024-12-05",
        }.items():
            self.assertEqual(block[key], value, key)
        self.assertEqual(U.check_layer(self.layer(), block, [TUD, TUBAF, SLUB]), [])
        self.assertEqual(R.validate_pairing(self.data), [])

    def test_output_is_deterministic(self):
        shared = repository_xml("r3d100000020", "Shared", [
            institution("TUBAF", ["general"], TUBAF), institution("TUD", ["general"], TUD)])
        self.run_update({"b.xml": shared, "a.xml": OPARA})
        first = self.snapshot()
        self.run_update({"a.xml": OPARA, "b.xml": shared})
        self.assertEqual(self.snapshot(), first)

    def test_successful_refresh_removes_stale_matches(self):
        shared = repository_xml("r3d100000020", "Shared", [institution("TUBAF", ["general"], TUBAF)])
        self.run_update({"a.xml": OPARA, "b.xml": shared})
        self.assertTrue((self.layer() / "records/031vc2293.json").exists())
        block = self.run_update({"a.xml": OPARA})
        self.assertFalse((self.layer() / "records/031vc2293.json").exists())
        self.assertFalse((self.layer() / "sources/r3d100000020.xml").exists())
        self.assertEqual(block["matched_ror_ids"], [TUD])
        self.assertEqual(list((self.data / "reuse").glob(".re3data-staging-*")), [])

    def assert_refused_and_unchanged(self, docs, pattern, **kw):
        self.run_update({"a.xml": OPARA})
        before = self.snapshot()
        with self.assertRaisesRegex(U.ExportError, pattern):
            self.run_update(docs, **kw)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(list((self.data / "reuse").glob(".re3data-staging-*")), [])

    def test_unknown_schema_keeps_previous_layer(self):
        future = repository_xml("r3d100012966", "OPARA", namespace="http://www.re3data.org/schema/5-0")
        self.assert_refused_and_unchanged({"a.xml": future}, "namespace")

    def test_broken_member_keeps_previous_layer(self):
        self.assert_refused_and_unchanged({"a.xml": OPARA, "b.xml": "<r3d:re3data"}, "invalid XML")

    def test_truncated_zip_keeps_previous_layer(self):
        self.run_update({"a.xml": OPARA})
        before = self.snapshot()
        path = self.make_zip({"a.xml": OPARA})
        path.write_bytes(path.read_bytes()[:-30])
        with self.assertRaisesRegex(U.ExportError, "ZIP"):
            U.update(path, self.data, retrieved="2026-10-28")
        self.assertEqual(self.snapshot(), before)

    def test_unexpected_member_keeps_previous_layer(self):
        self.assert_refused_and_unchanged({"a.xml": OPARA, "../evil.txt": "x"}, "unexpected member")

    def test_duplicate_id_keeps_previous_layer(self):
        self.assert_refused_and_unchanged({"a.xml": OPARA, "b.xml": OPARA}, "duplicate")

    def test_too_small_export_keeps_previous_layer(self):
        self.run_update({"a.xml": OPARA})
        before = self.snapshot()
        with mock.patch.object(U, "MIN_EXPORT_REPOSITORIES", 5):
            with self.assertRaisesRegex(U.ExportError, "only 1 repositories"):
                self.run_update({"a.xml": OPARA})
        self.assertEqual(self.snapshot(), before)

    def test_total_match_loss_is_refused_unless_allowed(self):
        unrelated = repository_xml("r3d100000099", "Elsewhere",
                                   [institution("X", ["general"], NOT_SAXON)])
        self.assert_refused_and_unchanged({"z.xml": unrelated}, "refusing to empty")
        block = self.run_update({"z.xml": unrelated}, allow_empty=True)
        self.assertEqual((block["matched"], block["missing"]), (0, 3))
        self.assertEqual(json.loads((self.layer() / "records.json").read_text()), [])
        self.assertEqual(list((self.layer() / "records").iterdir()), [])

    def test_first_run_without_matches_is_allowed(self):
        unrelated = repository_xml("r3d100000099", "Elsewhere",
                                   [institution("X", ["general"], NOT_SAXON)])
        block = self.run_update({"z.xml": unrelated})
        self.assertEqual(block["matched"], 0)

    def test_failed_meta_write_restores_previous_layer(self):
        self.run_update({"a.xml": OPARA})
        before = self.snapshot()
        shared = repository_xml("r3d100000020", "Shared", [institution("TUBAF", ["general"], TUBAF)])
        with mock.patch.object(R, "dump_json_atomic", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.run_update({"a.xml": OPARA, "b.xml": shared})
        self.assertEqual(self.snapshot(), before)

    def fail_replace(self, *, fail_src_names):
        """Patch os.replace to fail for sources with the given basenames."""
        real = U.os.replace

        def fake(src, dst):
            if Path(src).name in fail_src_names:
                raise OSError(f"injected failure moving {Path(src).name}")
            return real(src, dst)

        return mock.patch.object(U.os, "replace", side_effect=fake)

    def test_failed_layer_swap_restores_previous_layer(self):
        self.run_update({"a.xml": OPARA})
        before = self.snapshot()
        shared = repository_xml("r3d100000020", "Shared", [institution("TUBAF", ["general"], TUBAF)])
        with self.fail_replace(fail_src_names={"layer"}):
            with self.assertRaises(OSError):
                self.run_update({"a.xml": OPARA, "b.xml": shared})
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(list((self.data / "reuse").glob(".re3data-staging-*")), [])

    def test_failed_restore_keeps_previous_layer_in_staging(self):
        self.run_update({"a.xml": OPARA})
        old_record = (self.layer() / "records/042aqky30.json").read_bytes()
        shared = repository_xml("r3d100000020", "Shared", [institution("TUBAF", ["general"], TUBAF)])
        # The swap fails, and so does moving the previous layer back.
        err = io.StringIO()
        with self.fail_replace(fail_src_names={"layer", "previous"}), redirect_stderr(err):
            with self.assertRaises(OSError):
                self.run_update({"a.xml": OPARA, "b.xml": shared})
        [staging] = list((self.data / "reuse").glob(".re3data-staging-*"))
        self.assertIn(f"preserved at {staging / 'previous'}", err.getvalue())
        self.assertEqual((staging / "previous/records/042aqky30.json").read_bytes(), old_record)
        # Metadata was never replaced.
        self.assertEqual(R.read_meta(self.data)["reuse"]["re3data"]["matched_ror_ids"], [TUD])

    def test_other_companions_orphan_does_not_block_this_refresh(self):
        # A ROR update removed SLUB; the OpenAlex layer still holds its file.
        R.dump_json({"id": "https://openalex.org/I1"}, self.data / "reuse/openalex/records/03wf51b65.json")
        records = [{"id": TUD}, {"id": TUBAF}]
        R.dump_json(records, self.data / "records.json")
        (self.data / "records/03wf51b65.json").unlink()
        self.run_update({"a.xml": OPARA})
        self.assertEqual(U.own_pairing_problems(self.data), [])
        # The orphan is still reported where it belongs.
        self.assertEqual(
            R.validate_pairing(self.data, reuse_sources=["openalex"]),
            ["reuse/openalex/records/03wf51b65.json has no matching ROR record (orphan)"],
        )
        # And our own orphans are not hidden by the scoping.
        R.dump_json({}, self.layer() / "records/03wf51b65.json")
        self.assertEqual(
            U.own_pairing_problems(self.data),
            ["reuse/re3data/records/03wf51b65.json has no matching ROR record (orphan)"],
        )

    def test_check_layer_compares_projection_with_source(self):
        block = self.run_update({"a.xml": OPARA})
        src = self.layer() / "sources/r3d100012966.xml"
        src.write_text(OPARA.replace(
            "<r3d:responsibilityType>general</r3d:responsibilityType>",
            "<r3d:responsibilityType>funding</r3d:responsibilityType>",
        ))
        self.assertEqual(
            U.check_layer(self.layer(), block, [TUD, TUBAF, SLUB]),
            [f"re3data r3d100012966 for {TUD}: projection differs from source in matched_institutions"],
        )

    def test_check_layer_flags_source_linking_unprojected_record(self):
        block = self.run_update({"a.xml": OPARA})
        src = self.layer() / "sources/r3d100012966.xml"
        src.write_text(OPARA.replace(
            "<r3d:institutionName language=\"deu\">TU Dresden, Zentrum",
            "<r3d:institutionIdentifier><r3d:institutionIdentifierType>ROR</r3d:institutionIdentifierType>"
            f"<r3d:institutionIdentifierUrl>{SLUB}</r3d:institutionIdentifierUrl></r3d:institutionIdentifier>"
            "<r3d:institutionName language=\"deu\">TU Dresden, Zentrum",
        ))
        self.assertIn(
            f"re3data r3d100012966: source links {SLUB}, projection does not",
            U.check_layer(self.layer(), block, [TUD, TUBAF, SLUB]),
        )

    def test_check_layer_flags_unusable_source(self):
        block = self.run_update({"a.xml": OPARA})
        (self.layer() / "sources/r3d100012966.xml").write_text("<r3d:re3data")
        problems = U.check_layer(self.layer(), block, [TUD, TUBAF, SLUB])
        self.assertEqual(len(problems), 1)
        self.assertRegex(problems[0], r"source unusable: sources/r3d100012966\.xml: invalid XML")

    def test_check_layer_flags_tampering(self):
        block = self.run_update({"a.xml": OPARA})
        (self.layer() / "sources/r3d100012966.xml").unlink()
        R.dump_json({"ror_id": SLUB, "repositories": []}, self.layer() / "records/03wf51b65.json")
        problems = U.check_layer(self.layer(), block, [TUD, TUBAF, SLUB])
        self.assertIn("re3data source sources/r3d100012966.xml is missing", problems)
        self.assertIn("re3data records/03wf51b65.json has no records.json entry", problems)

    def test_check_layer_flags_wrong_counters(self):
        block = dict(self.run_update({"a.xml": OPARA}), relationships=2)
        self.assertEqual(
            U.check_layer(self.layer(), block, [TUD, TUBAF, SLUB]),
            ["meta reuse.re3data.relationships is 2, expected 1"],
        )


class CommittedLayerTests(unittest.TestCase):
    """The committed companion must agree with its own metadata."""

    def test_committed_layer_is_consistent(self):
        meta = R.read_meta()
        block = (meta.get("reuse") or {}).get("re3data")
        if block is None:
            self.skipTest("no re3data companion committed")
        ror_ids = U.load_ror_ids(R.DATA_DIR)
        # After a ROR update the layer is stale until its own refresh.
        if set(block["matched_ror_ids"]) | set(block["missing_ror_ids"]) != set(ror_ids):
            self.skipTest("re3data companion predates the current ROR subset")
        self.assertEqual(U.check_layer(R.REUSE_DIR / "re3data", block, ror_ids), [])


if __name__ == "__main__":
    unittest.main()
