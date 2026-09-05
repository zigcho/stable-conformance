import unittest
from unittest.mock import patch

from benchmarks.fixtures import packet
from integration.live import packet_ids
from integration.proxy import start
from runner import RunOptions, TargetState, run_transcripts


class LiveFixtureTests(unittest.TestCase):
    def test_boundary_evidence_keeps_actual_packet_order(self):
        self.assertEqual(packet_ids(packet(65, b"fixture") + packet(66, b"fixture")), [65, 66])
        with self.assertRaises(RuntimeError):
            packet_ids(b"\x41")

    def test_proxy_cannot_target_arbitrary_hosts(self):
        with self.assertRaises(ValueError):
            start(80, 443, "kai.ovh")

    def test_diagnostics_continue_only_with_verified_identities_and_keep_failure(self):
        states = {name: TargetState(name, None, {}, allows_mutation=True) for name in ("zigcho", "reference")}
        for verified in (False, True):
            with patch("runner._validate_complete_run_requirements", return_value=[]), \
                 patch("runner._run_session_preflights", return_value=[{"status": "failed", "identities_verified": verified}]), \
                 patch("runner._run_case", return_value={"id": "probe", "status": "passed"}) as case:
                report = run_transcripts([{"id": "probe"}], states,
                                        options=RunOptions(require_all=True, continue_on_failure=True))
                self.assertEqual(case.call_count, int(verified))
                if verified:
                    self.assertEqual(report["summary"]["failed_preflights"], 1)
                    self.assertEqual(report["summary"]["total"], 1)
                else:
                    self.assertEqual(report["summary"]["failed"], 1)
                    self.assertEqual(report["summary"]["total"], 0)


if __name__ == "__main__":
    unittest.main()
