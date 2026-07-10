#!/usr/bin/env python3

import importlib.util
import sys
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).with_name("readiness.py")
SPEC = importlib.util.spec_from_file_location("readiness", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("Unable to load readiness.py")
readiness = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = readiness
SPEC.loader.exec_module(readiness)


def judgment(status: str = "pass") -> dict[str, object]:
    return {
        "status": status,
        "rationale": "Evidence-backed test judgment.",
        "evidence": ["test fixture — deterministic evidence"],
        "confidence": "high",
    }


class ReadinessScoringTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.rubric = readiness.load_rubric(readiness.DEFAULT_RUBRIC)

    def assessment(self) -> dict[str, object]:
        criteria: dict[str, object] = {}
        for definition in self.rubric["criteria"]:
            if definition["scope"] == "repository":
                criteria[definition["id"]] = judgment()
            else:
                criteria[definition["id"]] = {
                    "applications": {
                        "backend": judgment(),
                        "frontend": judgment(),
                    }
                }
        return {
            "schema_version": "1.0",
            "rubric_version": "1.0",
            "repository": {
                "name": "fixture",
                "path": "/fixture",
                "commit": "abc123",
                "generated_at": "2026-07-10T12:00:00Z",
                "dirty": False,
                "applications": {
                    "backend": {"path": "backend", "description": "API"},
                    "frontend": {"path": "frontend", "description": "UI"},
                },
            },
            "preferences": {"source": "skill defaults", "overrides": []},
            "criteria": criteria,
        }

    def test_rubric_preserves_82_signal_scope_split(self) -> None:
        repository_count = sum(
            definition["scope"] == "repository"
            for definition in self.rubric["criteria"]
        )
        application_count = sum(
            definition["scope"] == "application"
            for definition in self.rubric["criteria"]
        )
        self.assertEqual(repository_count, 44)
        self.assertEqual(application_count, 38)

    def test_all_pass_assessment_scores_100_percent(self) -> None:
        assessment = self.assessment()
        scores = readiness.score_assessment(assessment, self.rubric)
        self.assertEqual(readiness.overall_percentage(scores, "owned_ratio"), 100)
        self.assertEqual(
            readiness.overall_percentage(scores, "compatibility_ratio"), 100
        )
        report = readiness.render_markdown(assessment, self.rubric, scores)
        payload = readiness.report_payload(assessment, self.rubric, scores)
        self.assertIn("**Owned readiness:** Level 5, **100.00%**", report)
        self.assertEqual(payload["summary"]["owned_percentage"], 100)

    def test_owned_score_excludes_inapplicable_app_but_compatibility_does_not(self) -> None:
        assessment = self.assessment()
        database_schema = assessment["criteria"]["database_schema"]
        database_schema["applications"]["frontend"] = judgment("not_applicable")

        scores = readiness.score_assessment(assessment, self.rubric)
        score = next(
            item for item in scores if item.criterion_id == "database_schema"
        )
        self.assertEqual(score.owned_ratio, 1)
        self.assertEqual(score.owned_denominator, 1)
        self.assertEqual(score.compatibility_ratio, 0.5)
        self.assertEqual(score.compatibility_denominator, 2)

    def test_non_skippable_criterion_cannot_be_not_applicable(self) -> None:
        assessment = self.assessment()
        assessment["criteria"]["readme"] = judgment("not_applicable")
        with self.assertRaisesRegex(
            readiness.AssessmentError, "non-skippable criterion"
        ):
            readiness.validate_assessment(assessment, self.rubric)

    def test_pass_requires_concrete_evidence(self) -> None:
        assessment = self.assessment()
        assessment["criteria"]["readme"]["evidence"] = []
        with self.assertRaisesRegex(readiness.AssessmentError, "evidence item"):
            readiness.validate_assessment(assessment, self.rubric)


if __name__ == "__main__":
    unittest.main()
