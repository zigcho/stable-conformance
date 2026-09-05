import copy
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from http_target import HttpResponse
from normalization import first_difference
from runner import TargetState, _run_step
from score_charts import compare
from transcript import TranscriptError, load_transcript


def fixture(target, index=18):
    total = 9000000 + index * 37 + 1
    site = "kai.ovh" if target == "zigcho" else "fixture.invalid"
    map_url = "https://kai.ovh/beatmapsets/1100000000" if target == "zigcho" else "https://osu.fixture.invalid/s/1100000000"
    pp = "126.064" if target == "zigcho" else "124.557"
    # Deliberately different product values, still valid wire data.
    aggregate = "126" if target == "zigcho" else "125"
    overall_rank = "1" if target == "zigcho" else "2"
    medals = "osu-combo-500+500 Combo+500 notes!" if target == "zigcho" else ""
    def entries(rank, performance):
        return (f"|rankBefore:|rankAfter:{rank}|rankedScoreBefore:|rankedScoreAfter:{total}"
                f"|totalScoreBefore:|totalScoreAfter:{total}|maxComboBefore:|maxComboAfter:600"
                f"|accuracyBefore:|accuracyAfter:100.0|ppBefore:|ppAfter:{performance}")
    return ("beatmapId:1100000000|beatmapSetId:1100000000|beatmapPlaycount:1|beatmapPasscount:1|approvedDate:2026-09-05 00:00:00|\n"
            f"|chartId:beatmap|chartUrl:{map_url}|chartName:Beatmap Ranking" + entries(1 if index == 18 else 2, pp) + "|onlineScoreId:2|\n"
            f"|chartId:overall|chartUrl:https://{site}/u/{10000 + index}|chartName:Overall Ranking" + entries(overall_rank, aggregate) + f"|achievements-new:{medals}")


class ScoreChartTests(unittest.TestCase):
    def compare_fixture(self, bodies, index=18):
        states = {target: SimpleNamespace(variables={"stable_delayed_user_id" if index == 18 else "user_id": 10000 + index,
                  "delayed_submitted_score_id" if index == 18 else "submitted_score_id": 2}) for target in bodies}
        return compare(bodies, states, "session-delayed-score" if index == 18 else "route-fixture-write", "delayed-submit" if index == 18 else "submit-score")

    def test_product_values_are_not_equality_requirements(self):
        for index in (18, 16):
            bodies = {target: {"status": 200, "body": fixture(target, index)} for target in ("zigcho", "reference")}
            projected, evidence = self.compare_fixture(bodies, index)
            self.assertIsNone(first_difference(projected["zigcho"], projected["reference"]))
            self.assertFalse(evidence["calculator_equivalence"])
            self.assertFalse(evidence["achievement_catalogue_equivalence"])
            self.assertIn("126.064", bodies["zigcho"]["body"])
        states = {}
        for target in ("zigcho", "reference"):
            client = Mock()
            client.request.return_value = HttpResponse(200, "OK", {}, fixture(target).encode(), 1.0)
            states[target] = TargetState(target, client, {"stable_delayed_user_id": 10018})
        step = {"id": "delayed-submit", "request": {"method": "POST", "path": "/web/osu-submit-modular-selector.php"},
                "capture": [{"from": "pipe_field", "name": "onlineScoreId", "as": "delayed_submitted_score_id", "type": "int", "secret": False}],
                "response": {"format": "text", "expect_status": 200, "score_chart_contract": "first-perfect-nm-20260905"}}
        report = _run_step({"id": "session-delayed-score"}, step, states, b"fixture-key", {})
        self.assertEqual(report["status"], "passed")
        self.assertFalse(report["score_contract"]["calculator_equivalence"])
        states["reference"].client.request.return_value = HttpResponse(200, "OK", {}, fixture("reference").replace("onlineScoreId:2", "onlineScoreId:7").encode(), 1.0)
        # Separate auto-increment sequences are identities, not gameplay values.
        for state in states.values():
            state.variables.pop("delayed_submitted_score_id", None)
        report = _run_step({"id": "session-delayed-score"}, step, states, b"fixture-key", {})
        self.assertEqual(report["status"], "passed")
        self.assertEqual(states["reference"].variables["delayed_submitted_score_id"], 7)

    def test_wrong_shared_values_and_broken_framing_still_fail(self):
        original = {target: {"status": 200, "body": fixture(target)} for target in ("zigcho", "reference")}
        for old, new in (("accuracyAfter:100.0", "accuracyAfter:90.0"),
                         ("maxComboAfter:600", "maxComboAfter:599"),
                         ("rankedScoreAfter:9000667", "rankedScoreAfter:0"),
                         ("ppAfter:126.064", "ppAfter:nan"),
                         ("rankBefore:", "rankBefore:0"),
                         ("onlineScoreId:2", "onlineScoreId:3"),
                         ("approvedDate:2026-09-05 00:00:00", "approvedDate:"),
                         ("|\n", "\n"), ("|onlineScoreId:2", "|onlineScoreId:2|onlineScoreId:2")):
            bodies = copy.deepcopy(original)
            bodies["zigcho"]["body"] = bodies["zigcho"]["body"].replace(old, new, 1)
            with self.subTest(old=old), self.assertRaises(TranscriptError):
                self.compare_fixture(bodies)
        for old, new in (("beatmapPlaycount:1", "beatmapPlaycount:2"),):
            bodies = copy.deepcopy(original)
            bodies["zigcho"]["body"] = bodies["zigcho"]["body"].replace(old, new)
            projected, _ = self.compare_fixture(bodies)
            self.assertIsNotNone(first_difference(projected["zigcho"], projected["reference"]))

    def test_contract_is_opt_in_only_on_the_two_submission_steps(self):
        for name, step_id in (("session-delayed-score", "delayed-submit"), ("route-fixture-write", "submit-score")):
            transcript = load_transcript(f"transcripts/{name}.json")
            self.assertEqual([step["id"] for step in transcript["steps"] if step.get("response", {}).get("score_chart_contract")], [step_id])
