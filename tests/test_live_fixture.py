import copy
import json
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from benchmarks.fixtures import packet, string
from integration.live import packet_ids, join_fixture_channel
from integration.proxy import start
from runner import RunOptions, TargetState, run_transcripts, _decode_body, _check_response_group, _login_bot_comparison_packets, _apply_captures, _run_step, _check_policy_matrix, _score_field_differences
from http_target import HttpResponse
from transcript import TranscriptError


class LiveFixtureTests(unittest.TestCase):
    def test_score_diagnostics_keep_values_private_and_do_not_hide_formatting(self):
        self.assertEqual(_score_field_differences("ppAfter:3.000|approvedDate:", "ppAfter:3.0|approvedDate:private"),
                         [{"line": 0, "field": "approvedDate"}, {"line": 0, "field": "ppAfter"}])

    def test_friend_membership_checks_survive_the_explicit_bot_mapping(self):
        transcript = json.loads((Path(__file__).resolve().parents[1] / "transcripts/packet-session-presence-chat.json").read_text())
        step = next(step for step in transcript["steps"] if step["id"] == "friend-add-readback")
        for wrong_peer in (False, True):
            states = {}
            for name, bot in (("zigcho", 3), ("reference", 1)):
                client = Mock()
                body = f"{bot}\n{10002 if wrong_peer else 10001}".encode()
                client.request.return_value = HttpResponse(200, "OK", {}, body, 1.0)
                states[name] = TargetState(name, client, {"stable_bot_user_id": bot, "stable_peer_user_id": 10001,
                    "stable_primary_username": "fixture", "stable_primary_password_md5": "fixture"})
            self.assertEqual(_run_step({}, step, states, b"fixture-key", {})["status"], "failed" if wrong_peer else "passed")

    def test_reconnect_allows_packet_types_not_duplicate_bootstrap_contents(self):
        for filename, step_id, variable in (
            ("session-login-reconnect.json", "immediate-reconnect", "stable_login_user_id"),
            ("session-delayed-score.json", "replacement-login", "stable_delayed_user_id"),
        ):
            transcript = json.loads((Path(__file__).resolve().parents[1] / "transcripts" / filename).read_text())
            matrix = next(step for step in transcript["steps"] if step["id"] == step_id)["policy_matrix"]
            initial = transcript["steps"][0]["policy_matrix"]["targets"]["zigcho"]
            self.assertEqual(matrix["targets"]["zigcho"]["required_packet_ids"], initial["required_packet_ids"][:3])
            packets = [{"id": packet_id, "payload": payload} for packet_id, payload in (
                (75, {}), (5, {"user_id": 10000}), (71, {}),
                (65, {"name": "#osu"}), (65, {"name": "#announce"}),
                (11, {"user_id": 10000, "pp": 10}), (11, {"user_id": 10001, "pp": 20}),
                (83, {"user_id": 10000}), (83, {"user_id": 10001}),
            )]
            canonical = {
                "zigcho": {"body": {"complete": True, "packets": packets}},
                "reference": {"body": {"complete": True, "packets": [
                    {"id": 5, "payload": {"user_id": -1}},
                    {"id": 24, "payload": {"message": "User already logged in."}},
                ]}},
            }
            states = {name: TargetState(name, None, {variable: 10000}) for name in canonical}
            results = {name: {"status": 200} for name in canonical}
            baseline = matrix["compare_target_with_step"]["zigcho"]
            history = {baseline: copy.deepcopy(canonical)}
            _check_policy_matrix(matrix, canonical, results, states, history)
            for mutation in ("duplicate", "changed", "missing"):
                changed = copy.deepcopy(canonical)
                rows = changed["zigcho"]["body"]["packets"]
                if mutation == "duplicate":
                    rows.append(copy.deepcopy(rows[-1]))
                elif mutation == "changed":
                    rows[5]["payload"]["pp"] = 999
                else:
                    rows.pop()
                with self.assertRaisesRegex(TranscriptError, "changed zigcho from baseline"):
                    _check_policy_matrix(matrix, changed, results, states, history)

    def test_peer_setup_requires_a_real_channel_join_acknowledgement(self):
        for acknowledgement in (packet(64, string("#osu")), b"", packet(64, string("#wrong"))):
            with patch("integration.live.request", side_effect=[(200, {}, b""), (200, {}, acknowledgement)]) as request:
                if acknowledgement == packet(64, string("#osu")):
                    actions = join_fixture_channel(18090, "fixture-token", "#osu")
                    self.assertEqual(actions[-1]["packet_ids"], [64])
                else:
                    with self.assertRaises(RuntimeError):
                        join_fixture_channel(18090, "fixture-token", "#osu")
                self.assertEqual(request.call_args_list[-1].args[1], packet(63, string("#osu")))

    def test_response_capture_failure_does_not_skip_the_other_target(self):
        states = {}
        for name, body in (("zigcho", b"invalid"), ("reference", b"onlineScoreId:42")):
            client = Mock()
            client.request.return_value = HttpResponse(200, "OK", {}, body, 1.0)
            states[name] = TargetState(name, client, {}, allows_mutation=True)
        step = {"id": "submit", "request": {"method": "POST", "path": "/"},
                "response": {"format": "text"},
                "capture": [{"from": "pipe_field", "name": "onlineScoreId", "as": "score_id", "type": "int"}]}
        result = _run_step({}, step, states, b"fixture-key", {})
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["failed_target"], "zigcho")
        self.assertEqual(set(result["targets"]), set(states))
        for state in states.values():
            self.assertEqual(state.client.request.call_count, 1)

    def test_score_id_capture_reads_one_exact_pipe_field(self):
        capture = {"from": "pipe_field", "name": "onlineScoreId", "as": "score_id", "type": "int", "secret": False}
        state = TargetState("fixture", None, {})
        body = "beatmapId:1|\n|chartId:beatmap|onlineScoreId:42|\n|chartId:overall|ppAfter:20"
        _apply_captures(None, {"body": body}, [capture], state)
        self.assertEqual(state.variables["score_id"], 42)
        for invalid in ("notOnlineScoreId:42", "onlineScoreId:", "onlineScoreId:2|onlineScoreId:3", "onlineScoreId:no"):
            with self.assertRaises(TranscriptError):
                _apply_captures(None, {"body": invalid}, [capture], state)

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

    def test_login_bot_branding_cannot_hide_scores_or_other_users(self):
        stats = {field: 0 for field in ("mods", "mode", "beatmap_id", "ranked_score", "accuracy", "play_count", "total_score", "global_rank", "pp")}
        stats.update(user_id=3, action=0, info_text="kai", beatmap_md5="")
        packets = [
            {"id": 11, "payload": stats},
            {"id": 83, "payload": {"user_id": 3, "username": "kai", "privileges": 31, "mode": 0, "global_rank": 0, "longitude": 0.0, "latitude": 0.0}},
            {"id": 72, "payload": {"user_ids": [3, 10000]}},
            {"id": 11, "payload": {"user_id": 10000, "pp": 123}},
        ]
        scoped = _login_bot_comparison_packets(packets, "zigcho")
        self.assertEqual(scoped[-1], packets[-1])
        self.assertEqual(scoped[0]["payload"]["user_ids"], ["<permanent-bot>", 10000])
        self.assertEqual(packets[2]["payload"]["user_ids"], [3, 10000])
        stats["pp"] = 1
        with self.assertRaises(TranscriptError):
            _login_bot_comparison_packets(packets, "zigcho")


if __name__ == "__main__":
    unittest.main()
