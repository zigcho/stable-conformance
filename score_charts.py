"""Strict shared-wire comparison for the pinned synthetic first-score fixture.

PP engines, achievement catalogues and site URLs intentionally differ. This is
not a general-purpose normalizer and does not claim calculator equivalence.
"""

import re

from transcript import TranscriptError


CONTRACT = "first-perfect-nm-20260905"
METRICS = ("rank", "rankedScore", "totalScore", "maxCombo", "accuracy", "pp")
ENTRIES = tuple(prefix + suffix for prefix in METRICS for suffix in ("Before", "After"))
KEYS = (
    ("beatmapId", "beatmapSetId", "beatmapPlaycount", "beatmapPasscount", "approvedDate"),
    ("chartId", "chartUrl", "chartName", *ENTRIES, "onlineScoreId"),
    ("chartId", "chartUrl", "chartName", *ENTRIES, "achievements-new"),
)


def decode(body):
    if not isinstance(body, str) or len(body) > 65536:
        raise TranscriptError("score chart is absent or exceeds the fixture bound")
    lines = body.split("\n")
    if len(lines) != 3 or not lines[0].endswith("|") or not lines[1].startswith("|") or not lines[1].endswith("|") or not lines[2].startswith("|"):
        raise TranscriptError("score chart line delimiters differ from the Stable contract")
    result = []
    for line, keys in zip((lines[0][:-1], lines[1][1:-1], lines[2][1:]), KEYS):
        fields = [field.partition(":") for field in line.split("|")]
        if tuple(field[0] for field in fields) != keys or any(not field[1] for field in fields):
            raise TranscriptError("score chart fields are missing, duplicated or out of order")
        result.append({key: value for key, _, value in fields})
    return result


def compare(canonical, states, case_id, step_id):
    """Return comparison copies plus explicit, bounded evidence of divergence."""
    import copy

    index = {("session-delayed-score", "delayed-submit"): 18,
             ("route-fixture-write", "submit-score"): 16}.get((case_id, step_id))
    if index is None or set(canonical) != {"zigcho", "reference"}:
        raise TranscriptError("score chart contract is only defined for the two pinned first scores")
    projected = copy.deepcopy(canonical)
    evidence = {}
    for target in ("zigcho", "reference"):
        variables = states[target].variables
        user_key = "stable_delayed_user_id" if index == 18 else "user_id"
        if variables.get(user_key) != 10000 + index:
            raise TranscriptError("score chart fixture identity changed; review its expected contract")
        rows = decode(canonical[target].get("body"))
        total = str(9000000 + index * 37 + 1)
        expected = (
            {"beatmapId": "1100000000", "beatmapSetId": "1100000000", "approvedDate": "2026-09-05 00:00:00"},
            {"chartId": "beatmap", "chartName": "Beatmap Ranking", "rankAfter": "1" if index == 18 else "2", "rankedScoreAfter": total,
             "totalScoreAfter": total, "maxComboAfter": "600", "accuracyAfter": "100.0",
             "chartUrl": "https://kai.ovh/beatmapsets/1100000000" if target == "zigcho" else "https://osu.fixture.invalid/s/1100000000"},
            {"chartId": "overall", "chartName": "Overall Ranking", "rankedScoreAfter": total,
             "totalScoreAfter": total, "maxComboAfter": "600", "accuracyAfter": "100.0",
             "chartUrl": f"https://{'kai.ovh' if target == 'zigcho' else 'fixture.invalid'}/u/{10000 + index}"},
        )
        for row_number, values in enumerate(expected):
            for key, wanted in values.items():
                if rows[row_number][key] != wanted:
                    raise TranscriptError(f"{target} score chart fixture mismatch at line {row_number} field {key}")
        for row in rows[1:]:
            if any(row[prefix + "Before"] != "" for prefix in METRICS):
                raise TranscriptError(f"{target} first score has unexpected nonempty before values")
        for key in ("beatmapPlaycount", "beatmapPasscount"):
            if not re.fullmatch(r"[1-9][0-9]{0,9}", rows[0][key]):
                raise TranscriptError(f"{target} score chart has invalid map counters")
        if not re.fullmatch(r"[1-9][0-9]{0,18}", rows[1]["onlineScoreId"]):
            raise TranscriptError(f"{target} score chart has invalid score identity")
        for row in rows[1:]:
            if row["ppAfter"] and not re.fullmatch(r"[0-9]{1,12}(?:\.[0-9]{1,3})?", row["ppAfter"]):
                raise TranscriptError(f"{target} score chart has invalid pp framing")
        if not re.fullmatch(r"[1-9][0-9]{0,9}", rows[2]["rankAfter"]):
            raise TranscriptError(f"{target} score chart has invalid aggregate rank framing")
        medals = rows[2]["achievements-new"].split("/") if rows[2]["achievements-new"] else []
        slugs = []
        for medal in medals:
            parts = medal.split("+")
            if len(parts) != 3 or not re.fullmatch(r"[a-z0-9-]{1,96}", parts[0]) or any(not part or len(part) > 1024 or any(ord(c) < 32 for c in part) for part in parts):
                raise TranscriptError(f"{target} score chart has invalid achievement framing")
            slugs.append(parts[0])
        if len(slugs) > 128 or len(slugs) != len(set(slugs)):
            raise TranscriptError(f"{target} score chart repeats achievements or exceeds its bound")
        evidence[target] = {"pp": rows[1]["ppAfter"], "aggregate_pp": rows[2]["ppAfter"],
                            "aggregate_rank": rows[2]["rankAfter"], "achievement_count": len(slugs)}
        # Product-specific values have a wire-shape check, not a parity oracle.
        # All shared values, field ordering and framing remain mandatory.
        for row in rows[1:]:
            row["chartUrl"] = "<checked-site-url>"
            row["ppAfter"] = "<product-pp-not-compared>"
        rows[2]["rankAfter"] = "<product-ranking-not-compared>"
        rows[2]["achievements-new"] = "<catalogue-content-not-compared>"
        projected[target]["body"] = rows
    return projected, {
        "contract": CONTRACT,
        "comparison": "shared_score_wire_with_declared_product_differences",
        "calculator_equivalence": False,
        "achievement_catalogue_equivalence": False,
        "targets": evidence,
    }
