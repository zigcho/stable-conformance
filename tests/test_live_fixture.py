import unittest
from unittest.mock import patch

from benchmarks.fixtures import packet
from integration.live import packet_ids
from integration.proxy import start
from runner import RunOptions, TargetState, run_transcripts, _decode_body, _check_response_group
from transcript import TranscriptError


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

    def test_bot_identity_mapping_preserves_every_other_field(self):
        self.assertEqual(_decode_body(b"3\n10000\n", "user_id_lines"),
                         {"users": [{"user_id": 3}, {"user_id": 10000}], "trailing_newline": True})
        with self.assertRaises(TranscriptError):
            _decode_body(b"3\ninvalid", "user_id_lines")
        states = {name: TargetState(name, None, {"bot": user_id}) for name, user_id in (("zigcho", 3), ("reference", 1))}
        group = {"id": "bot", "steps": ["action", "drain"], "normalizers": [
            {"kind": "variable", "path": "body.packets.0.payload.sender_id", "variable": "bot"}]}
        history = {"action": {}, "drain": {}}
        for name, user_id in (("zigcho", 3), ("reference", 1)):
            history["action"][name] = {"body": {"complete": True, "consumed": 1, "packets": [
                {"id": 7, "payload": {"sender_id": user_id, "text": "created"}}]}}
            history["drain"][name] = {"body": {"complete": True, "consumed": 0, "packets": []}}
        self.assertEqual(_check_response_group(group, history, states)["status"], "passed")
        history["action"]["reference"]["body"]["packets"][0]["payload"]["text"] = "wrong message"
        self.assertEqual(_check_response_group(group, history, states)["status"], "failed")
        history["action"]["reference"]["body"]["packets"][0]["payload"]["sender_id"] = 99
        self.assertEqual(_check_response_group(group, history, states)["status"], "failed")


if __name__ == "__main__":
    unittest.main()
