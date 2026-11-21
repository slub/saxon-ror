"""Network-free checks for independent OpenAlex companion updates."""

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import ror_lib as R
import update_openalex as U


class OpenalexPublishTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.data = Path(tmp.name) / "data"
        self.layer = self.data / "reuse/openalex"
        self.ror = "https://ror.org/042aqky30"
        R.dump_json([{"id": self.ror}], self.data / "records.json")
        R.dump_json({"id": self.ror}, self.data / "records/042aqky30.json")
        R.write_meta({"ror": {"record_count": 1}}, self.data)
        R.write_meta({"matched": 1}, self.data / "reuse/re3data")
        # Another companion can legitimately await its post-ROR refresh.
        R.dump_json({}, self.data / "reuse/re3data/records/03wf51b65.json")
        R.dump_json({}, self.layer / "records/03wf51b65.json")
        for target, name, value in ((R, "DATA_DIR", self.data), (U, "OPENALEX_DIR", self.layer)):
            patcher = mock.patch.object(target, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def snapshot(self):
        return {str(p.relative_to(self.data)): p.read_bytes()
                for p in self.data.rglob("*") if p.is_file()}

    def run_update(self, **fetch_args):
        with mock.patch.object(sys, "argv", ["update_openalex.py"]), \
                mock.patch.object(U, "fetch_batch", **fetch_args), \
                redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            return U.main()

    def test_refresh_writes_only_own_layer_and_metadata(self):
        before = self.snapshot()
        entity = {"id": "https://openalex.org/I1", "ror": self.ror, "works_count": 7}
        self.assertEqual(self.run_update(return_value=[entity]), 0)
        for path, content in before.items():
            if not path.startswith("reuse/openalex/"):
                self.assertEqual((self.data / path).read_bytes(), content)
        self.assertFalse((self.layer / "records/03wf51b65.json").exists())
        self.assertEqual(json.loads((self.layer / "records.json").read_text()), [entity])
        meta = R.read_meta(self.layer)
        self.assertEqual((meta["matched"], meta["missing"], meta["ror_total"]), (1, 0, 1))
        self.assertEqual(meta["missing_ror_ids"], [])
        self.assertEqual(R.validate_pairing(self.data, reuse_sources=["openalex"]), [])

    def test_failed_fetch_keeps_existing_data_and_metadata(self):
        R.write_meta({"matched": 1, "retrieved": "2026-11-01"}, self.layer)
        before = self.snapshot()
        with self.assertRaisesRegex(RuntimeError, "unavailable"):
            self.run_update(side_effect=RuntimeError("unavailable"))
        self.assertEqual(self.snapshot(), before)
