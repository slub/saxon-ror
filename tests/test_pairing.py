"""Network-free tests for ROR and reuse pairing validation."""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import ror_lib as R


class PairingTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.data = Path(tmp.name)
        self.record = {"id": "https://ror.org/042aqky30"}
        R.dump_json([self.record], self.data / "records.json")
        R.dump_json(self.record, self.data / "records/042aqky30.json")

    def test_default_reports_reuse_orphan(self):
        R.dump_json({}, self.data / "reuse/openalex/records/0245cg223.json")
        self.assertEqual(R.validate_pairing(self.data), [
            "reuse/openalex/records/0245cg223.json "
            "has no matching ROR record (orphan)"
        ])

    def test_ror_only_tolerates_reuse_orphan(self):
        R.dump_json({}, self.data / "reuse/openalex/records/0245cg223.json")
        self.assertEqual(R.validate_pairing(self.data, include_reuse=False), [])

    def test_missing_ror_file_fails_in_both_modes(self):
        (self.data / "records/042aqky30.json").unlink()
        for include_reuse in (True, False):
            with self.subTest(include_reuse=include_reuse):
                self.assertEqual(
                    R.validate_pairing(self.data, include_reuse=include_reuse),
                    ["records.json entry 042aqky30 has no per-record file"],
                )

    def test_orphan_ror_file_fails_in_both_modes(self):
        R.dump_json({}, self.data / "records/0245cg223.json")
        for include_reuse in (True, False):
            with self.subTest(include_reuse=include_reuse):
                self.assertEqual(
                    R.validate_pairing(self.data, include_reuse=include_reuse),
                    ["records/0245cg223.json has no records.json entry"],
                )
