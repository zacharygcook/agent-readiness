#!/usr/bin/env python3

from __future__ import annotations

import copy
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock


MODULE_PATH = Path(__file__).with_name("readiness.py")
SKILL_ROOT = MODULE_PATH.parent.parent
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


def run_command(*arguments: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    environment = dict(os.environ)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    return subprocess.run(
        [*arguments],
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )


def run_cli(*arguments: str) -> subprocess.CompletedProcess[str]:
    return run_command(sys.executable, str(MODULE_PATH), *arguments)


def initialize_git_repository(path: Path, *, with_preferences: bool = False) -> None:
    path.mkdir(parents=True)
    (path / "backend").mkdir()
    (path / "frontend").mkdir()
    (path / "README.md").write_text("# Fixture\n", encoding="utf-8")
    (path / "backend" / "package.json").write_text('{"name":"backend"}\n', encoding="utf-8")
    (path / "frontend" / "package.json").write_text('{"name":"frontend"}\n', encoding="utf-8")
    if with_preferences:
        (path / "AGENT_READINESS_PREFERENCES.md").write_text(
            "# Agent Readiness Preferences\n", encoding="utf-8"
        )
    for command in (
        ("git", "init", "-b", "master"),
        ("git", "config", "user.email", "fixture@example.com"),
        ("git", "config", "user.name", "Fixture"),
        ("git", "add", "README.md", "backend/package.json", "frontend/package.json"),
    ):
        result = run_command(*command, cwd=path)
        if result.returncode != 0:
            raise RuntimeError(result.stderr)
    if with_preferences:
        result = run_command("git", "add", "AGENT_READINESS_PREFERENCES.md", cwd=path)
        if result.returncode != 0:
            raise RuntimeError(result.stderr)
    result = run_command("git", "commit", "-m", "Create fixture", cwd=path)
    if result.returncode != 0:
        raise RuntimeError(result.stderr)


def create_fake_browser(path: Path) -> None:
    path.write_text(
        "#!/usr/bin/env python3\n"
        "import pathlib, sys\n"
        "target = next(value.split('=', 1)[1] for value in sys.argv if value.startswith('--print-to-pdf='))\n"
        "pathlib.Path(target).write_bytes(b'%PDF-1.4\\n' + b'x' * 2048)\n",
        encoding="utf-8",
    )
    path.chmod(0o755)


class ReadinessScoringTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.rubric = readiness.load_rubric(readiness.DEFAULT_RUBRIC)

    def assessment(self) -> dict[str, Any]:
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

    def write_json(self, path: Path, value: dict[str, Any]) -> None:
        path.write_text(f"{json.dumps(value, indent=2)}\n", encoding="utf-8")

    def test_skill_package_has_required_metadata_and_resources(self) -> None:
        skill = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
        self.assertTrue(skill.startswith("---\nname: agent-readiness\n"))
        self.assertIn("description:", skill.split("---", 2)[1])
        self.assertTrue(readiness.DEFAULT_RUBRIC.is_file())
        self.assertTrue(readiness.PREFERENCES_TEMPLATE.is_file())
        self.assertEqual(readiness.package_version(), "0.4.0")
        self.assertEqual(len(readiness.package_fingerprint()), 64)
        self.assertEqual(
            readiness.PREFERENCES_TEMPLATE.name,
            "DEFAULT_AGENT_READINESS_PREFERENCES.md",
        )

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

    def test_rubric_rejects_duplicate_ids(self) -> None:
        rubric = copy.deepcopy(self.rubric)
        rubric["criteria"][1]["id"] = rubric["criteria"][0]["id"]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "rubric.json"
            self.write_json(path, rubric)
            with self.assertRaisesRegex(readiness.AssessmentError, "82 unique criteria"):
                readiness.load_rubric(path)

    def test_rubric_rejects_invalid_definition_shape(self) -> None:
        rubric = copy.deepcopy(self.rubric)
        rubric["criteria"][0]["scope"] = "workspace"
        rubric["criteria"][0]["level"] = 8
        rubric["criteria"][0]["guidance"] = ""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "rubric.json"
            self.write_json(path, rubric)
            with self.assertRaisesRegex(readiness.AssessmentError, "Rubric validation failed") as context:
                readiness.load_rubric(path)
        self.assertIn("scope must be repository or application", str(context.exception))
        self.assertIn("level must be an integer from 1 through 5", str(context.exception))
        self.assertIn("guidance must be a non-empty string", str(context.exception))

    def test_rubric_rejects_non_string_ids_without_crashing(self) -> None:
        rubric = copy.deepcopy(self.rubric)
        rubric["criteria"][0]["id"] = {"invalid": "object"}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "rubric.json"
            self.write_json(path, rubric)
            with self.assertRaisesRegex(readiness.AssessmentError, "82 unique criteria"):
                readiness.load_rubric(path)

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

    def test_owned_build_deploy_extension_is_separate_from_82_criterion_scores(self) -> None:
        assessment = self.assessment()
        assessment["owned_extensions"] = {
            "build_deploy_performance_hygiene": {
                "version": "1.0",
                "controls": {
                    control_id: judgment()
                    for control_id in readiness.OWNED_EXTENSION_DEFINITIONS[
                        "build_deploy_performance_hygiene"
                    ]["controls"]
                },
            }
        }

        scores = readiness.score_assessment(assessment, self.rubric)
        report = readiness.render_markdown(assessment, self.rubric, scores)
        payload = readiness.report_payload(assessment, self.rubric, scores)

        self.assertEqual(readiness.overall_percentage(scores, "owned_ratio"), 100)
        self.assertEqual(payload["owned_extensions"]["checkpoint_denominator"], 1)
        self.assertEqual(payload["owned_extensions"]["control_denominator"], 5)
        self.assertEqual(payload["owned_extensions"]["controls_passing"], 5)
        self.assertIn("do not change the stable 82-criterion", report)
        self.assertIn("control denominator: 5", report)
        self.assertIn("01a / Owned extensions", readiness.render_html(payload))

    def test_owned_build_deploy_extension_requires_every_evidence_backed_control(self) -> None:
        assessment = self.assessment()
        assessment["owned_extensions"] = {
            "build_deploy_performance_hygiene": {
                "version": "1.0",
                "controls": {"phase_timing": judgment()},
            }
        }

        with self.assertRaisesRegex(readiness.AssessmentError, "missing controls"):
            readiness.validate_assessment(assessment, self.rubric)

    def test_owned_build_deploy_extension_rejects_a_prose_only_control(self) -> None:
        assessment = self.assessment()
        controls = {
            control_id: judgment()
            for control_id in readiness.OWNED_EXTENSION_DEFINITIONS[
                "build_deploy_performance_hygiene"
            ]["controls"]
        }
        controls["phase_timing"]["evidence"] = []
        assessment["owned_extensions"] = {
            "build_deploy_performance_hygiene": {"version": "1.0", "controls": controls}
        }

        with self.assertRaisesRegex(readiness.AssessmentError, "at least one evidence item"):
            readiness.validate_assessment(assessment, self.rubric)

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

    def test_fully_inapplicable_app_criterion_is_skipped_in_both_scores(self) -> None:
        assessment = self.assessment()
        database_schema = assessment["criteria"]["database_schema"]
        database_schema["applications"]["backend"] = judgment("not_applicable")
        database_schema["applications"]["frontend"] = judgment("not_applicable")

        scores = readiness.score_assessment(assessment, self.rubric)
        score = next(
            item for item in scores if item.criterion_id == "database_schema"
        )
        self.assertIsNone(score.owned_ratio)
        self.assertIsNone(score.compatibility_ratio)
        self.assertEqual(set(score.skipped_units), {"backend", "frontend"})

    def test_mixed_application_results_score_fractionally(self) -> None:
        assessment = self.assessment()
        lint = assessment["criteria"]["lint_config"]
        lint["applications"]["frontend"] = judgment("fail")
        score = next(
            item
            for item in readiness.score_assessment(assessment, self.rubric)
            if item.criterion_id == "lint_config"
        )
        self.assertEqual(score.owned_ratio, 0.5)
        self.assertEqual(score.compatibility_ratio, 0.5)
        self.assertEqual(score.failing_units, ("frontend",))

    def test_readiness_level_boundaries_are_stable(self) -> None:
        expectations = {
            0: 1,
            19.999: 1,
            20: 2,
            39.999: 2,
            40: 3,
            59.999: 3,
            60: 4,
            79.999: 4,
            80: 5,
            100: 5,
        }
        for percentage, expected in expectations.items():
            with self.subTest(percentage=percentage):
                self.assertEqual(readiness.readiness_level(percentage), expected)

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

    def test_assessment_requires_every_criterion(self) -> None:
        assessment = self.assessment()
        del assessment["criteria"]["readme"]
        with self.assertRaisesRegex(readiness.AssessmentError, "Missing criteria: readme"):
            readiness.validate_assessment(assessment, self.rubric)

    def test_assessment_versions_must_match_schema_and_rubric(self) -> None:
        assessment = self.assessment()
        assessment["schema_version"] = "2.0"
        assessment["rubric_version"] = "outdated"
        with self.assertRaisesRegex(readiness.AssessmentError, "schema_version must be 1.0") as context:
            readiness.validate_assessment(assessment, self.rubric)
        self.assertIn("rubric_version must match", str(context.exception))

    def test_application_judgments_must_match_discovered_applications(self) -> None:
        assessment = self.assessment()
        del assessment["criteria"]["lint_config"]["applications"]["frontend"]
        with self.assertRaisesRegex(readiness.AssessmentError, "missing apps: frontend"):
            readiness.validate_assessment(assessment, self.rubric)

    def test_cli_list_emits_all_criteria(self) -> None:
        result = run_cli("list")
        self.assertEqual(result.returncode, 0, result.stderr)
        lines = result.stdout.strip().splitlines()
        self.assertEqual(len(lines), 82)
        self.assertTrue(lines[0].startswith("large_file_detection\trepository\tL3"))
        self.assertTrue(lines[-1].startswith("error_to_insight_pipeline\tapplication\tL5"))

    def test_cli_preferences_copies_defaults_and_refuses_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "nested" / "AGENT_READINESS_PREFERENCES.md"
            first = run_cli("preferences", "--output", str(output))
            second = run_cli("preferences", "--output", str(output))
            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertEqual(
                output.read_text(encoding="utf-8"),
                readiness.PREFERENCES_TEMPLATE.read_text(encoding="utf-8"),
            )
            self.assertEqual(second.returncode, 1)
            self.assertIn("Refusing to overwrite existing file", second.stderr)

    def test_cli_init_records_git_state_applications_and_root_preferences(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository = root / "repo"
            initialize_git_repository(repository, with_preferences=True)
            output = root / "assessment.json"
            result = run_cli(
                "init",
                "--repo",
                str(repository),
                "--app",
                "backend=backend",
                "--app",
                "frontend=frontend",
                "--output",
                str(output),
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            assessment = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(assessment["preferences"]["source"], "AGENT_READINESS_PREFERENCES.md")
            self.assertEqual(set(assessment["repository"]["applications"]), {"backend", "frontend"})
            self.assertFalse(assessment["repository"]["dirty"])
            self.assertEqual(assessment["preferences"]["checksum"], readiness.sha256_file(repository / "AGENT_READINESS_PREFERENCES.md"))
            self.assertEqual(assessment["provenance"]["skill_version"], "0.4.0")
            self.assertEqual(assessment["provenance"]["applications"], ["backend", "frontend"])
            self.assertEqual(assessment["provenance"]["evidence_checks"], [])
            self.assertEqual(assessment["recommendations"], [])
            self.assertEqual(len(assessment["criteria"]), 82)
            self.assertEqual(assessment["criteria"]["readme"]["status"], "unscored")

    def test_cli_init_refuses_duplicate_application_ids(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository = root / "repo"
            initialize_git_repository(repository)
            result = run_cli(
                "init",
                "--repo",
                str(repository),
                "--app",
                "app=backend",
                "--app",
                "app=frontend",
                "--output",
                str(root / "assessment.json"),
            )
            self.assertEqual(result.returncode, 1)
            self.assertIn("Application IDs must be unique", result.stderr)

    def test_cli_validate_reports_invalid_assessment(self) -> None:
        assessment = self.assessment()
        assessment["criteria"]["readme"]["confidence"] = "certain"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "assessment.json"
            self.write_json(path, assessment)
            result = run_cli("validate", "--assessment", str(path))
            self.assertEqual(result.returncode, 1)
            self.assertIn("confidence must be high, medium, or low", result.stderr)

    def test_cli_score_writes_markdown_and_json_with_known_math(self) -> None:
        assessment = self.assessment()
        assessment["criteria"]["readme"] = judgment("fail")
        database_schema = assessment["criteria"]["database_schema"]
        database_schema["applications"]["frontend"] = judgment("not_applicable")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "assessment.json"
            output = root / "report"
            browser = root / "fake-chromium"
            create_fake_browser(browser)
            self.write_json(path, assessment)
            result = run_cli(
                "score", "--assessment", str(path), "--output-dir", str(output),
                "--pdf", "--browser", str(browser),
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            markdown = (output / "agent-readiness-report.md").read_text(encoding="utf-8")
            html = (output / "agent-readiness-report.html").read_text(encoding="utf-8")
            payload = json.loads((output / "agent-readiness-report.json").read_text(encoding="utf-8"))
            self.assertIn("**README** (`readme`, Level 1)", markdown)
            self.assertIn("Owned: Level 5 (98.78%)", result.stdout)
            self.assertEqual(payload["summary"]["owned_percentage"], 98.7805)
            self.assertEqual(payload["summary"]["compatibility_percentage"], 98.1707)
            self.assertEqual(payload["criteria"]["readme"]["failing_units"], ["repository"])
            self.assertEqual(payload["criteria"]["database_schema"]["skipped_units"], ["frontend"])
            self.assertIn("<!doctype html>", html)
            self.assertIn("01 / Category health", html)
            self.assertIn("03 / Priority queue", html)
            self.assertIn("04 / Application surface", html)
            self.assertIn("class=\"category-track\"", html)
            self.assertIn("README", html)
            self.assertTrue((output / "agent-readiness-report.pdf").is_file())
            self.assertIn("PDF:", result.stdout)
            self.assertEqual(payload["report_version"], "2.0")
            self.assertEqual(len(payload["summary"]["categories"]), 9)
            self.assertEqual(payload["summary"]["status_counts"]["fail"], 1)
            self.assertEqual(payload["summary"]["category_breakdown"]["documentation"]["fail"], 1)
            self.assertEqual(payload["applications"]["backend"]["percentage"], 100)
            self.assertEqual(payload["applications"]["frontend"]["not_applicable"], 1)
            self.assertEqual(payload["next_actions"][0]["source"], "fallback")
            self.assertIn("no structured provenance", payload["audit_warnings"][0])

    def test_provenance_rejects_oversized_or_multiline_evidence_checks(self) -> None:
        assessment = self.assessment()
        assessment["provenance"] = {
            "audit_timestamp": "2026-07-10T12:00:00Z",
            "rubric_checksum": "abc",
            "preferences_checksum": "def",
            "skill_version": "0.2.0",
            "skill_fingerprint": "123",
            "repository_commit": "abc123",
            "repository_dirty": False,
            "applications": ["backend", "frontend"],
            "evidence_checks": [{
                "kind": "command",
                "label": "tests",
                "checked_at": "2026-07-10T12:00:00Z",
                "exit_status": 0,
                "command": "yarn test\necho secret",
                "summary": "passed",
            }],
        }
        with self.assertRaisesRegex(readiness.AssessmentError, "one non-empty line"):
            readiness.validate_assessment(assessment, self.rubric)

    def test_external_provenance_surfaces_stale_evidence(self) -> None:
        warnings = readiness.provenance_warnings({
            "evidence_checks": [{
                "kind": "external",
                "label": "branch protection",
                "fresh_until": "2020-01-01T00:00:00Z",
            }]
        })
        self.assertEqual(len(warnings), 1)
        self.assertIn("branch protection", warnings[0])
        self.assertIn("stale", warnings[0])

    def test_comparison_promotes_regressions_and_tracks_evidence_changes(self) -> None:
        before_assessment = self.assessment()
        after_assessment = copy.deepcopy(before_assessment)
        before_assessment["criteria"]["lint_config"]["applications"]["frontend"] = judgment("fail")
        after_assessment["criteria"]["readme"] = judgment("fail")
        after_assessment["criteria"]["lint_config"]["applications"]["frontend"]["evidence"] = ["new lint proof"]
        before = readiness.report_payload(before_assessment, self.rubric, readiness.score_assessment(before_assessment, self.rubric))
        after = readiness.report_payload(after_assessment, self.rubric, readiness.score_assessment(after_assessment, self.rubric))
        comparison = readiness.comparison_payload(before, after)
        self.assertIn("readme", comparison["regressions"])
        self.assertIn("lint_config", comparison["improvements"])
        self.assertEqual(comparison["criteria"]["readme"]["regressions"], ["repository"])
        self.assertEqual(comparison["criteria"]["lint_config"]["newly_passing_units"], ["frontend"])
        self.assertEqual(comparison["criteria"]["lint_config"]["evidence_changes"], ["frontend"])
        self.assertIn("## Regressions", readiness.render_comparison_markdown(comparison))
        self.assertIn("class=\"regression\"", readiness.render_comparison_html(comparison))

    def test_cli_compare_accepts_assessments_and_writes_three_formats(self) -> None:
        before = self.assessment()
        after = copy.deepcopy(before)
        after["criteria"]["readme"] = judgment("fail")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            before_path = root / "before.json"
            after_path = root / "after.json"
            output = root / "comparison"
            browser = root / "fake-chromium"
            create_fake_browser(browser)
            self.write_json(before_path, before)
            self.write_json(after_path, after)
            result = run_cli(
                "compare", "--before", str(before_path), "--after", str(after_path),
                "--output-dir", str(output), "--pdf", "--browser", str(browser),
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("Regressions: 1", result.stdout)
            self.assertTrue((output / "agent-readiness-comparison.md").is_file())
            self.assertTrue((output / "agent-readiness-comparison.json").is_file())
            self.assertTrue((output / "agent-readiness-comparison.html").is_file())
            self.assertTrue((output / "agent-readiness-comparison.pdf").is_file())

    def test_ranked_recommendations_drive_report_actions_and_authority_badges(self) -> None:
        assessment = self.assessment()
        assessment["criteria"]["readme"] = judgment("fail")
        assessment["recommendations"] = [{
            "criterion_id": "readme",
            "priority": 1,
            "rationale": "Accurate setup guidance unlocks safe first-run agent work.",
            "effort": "small",
            "authority": "autonomous",
            "flags": [],
        }]
        scores = readiness.score_assessment(assessment, self.rubric)
        payload = readiness.report_payload(assessment, self.rubric, scores)
        report = readiness.render_html(payload)
        self.assertTrue(payload["recommendations_ranked"])
        self.assertEqual(payload["next_actions"][0]["criterion_id"], "readme")
        self.assertEqual(payload["next_actions"][0]["authority"], "autonomous")
        self.assertIn("authority-autonomous", report)
        self.assertIn("Accurate setup guidance", report)

    def test_recommendations_require_known_unique_criteria_and_authority(self) -> None:
        assessment = self.assessment()
        assessment["recommendations"] = [{
            "criterion_id": "not_real",
            "priority": 1,
            "rationale": "Invalid fixture.",
            "effort": "tiny",
            "authority": "silent_mutation",
            "flags": ["cheap_score"],
        }]
        with self.assertRaisesRegex(readiness.AssessmentError, r"recommendations\[0\]") as context:
            readiness.validate_assessment(assessment, self.rubric)
        self.assertIn("criterion_id", str(context.exception))
        self.assertIn("effort", str(context.exception))
        self.assertIn("authority", str(context.exception))
        self.assertIn("flags", str(context.exception))

    def test_cli_score_embeds_progress_from_previous_round(self) -> None:
        before = self.assessment()
        before["criteria"]["readme"] = judgment("fail")
        after = self.assessment()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            before_path = root / "before.json"
            after_path = root / "after.json"
            output = root / "report"
            self.write_json(before_path, before)
            self.write_json(after_path, after)
            result = run_cli(
                "score", "--assessment", str(after_path), "--previous", str(before_path),
                "--output-dir", str(output),
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads((output / "agent-readiness-report.json").read_text(encoding="utf-8"))
            html = (output / "agent-readiness-report.html").read_text(encoding="utf-8")
            markdown = (output / "agent-readiness-report.md").read_text(encoding="utf-8")
            self.assertEqual(payload["progress"]["summary"]["improvement_count"], 1)
            self.assertIn("02 / Progress", html)
            self.assertIn("README", html)
            self.assertIn("Progress Since Previous Round", markdown)
            self.assertIn("+1.22 points", markdown)

    def test_doctor_reports_package_and_root_preferences(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = Path(directory) / "repo"
            initialize_git_repository(repository, with_preferences=True)
            result = run_cli("doctor", "--repo", str(repository), "--json")
            self.assertEqual(result.returncode, 0, result.stderr)
            report = json.loads(result.stdout)
            self.assertTrue(report["ok"])
            checks = {check["name"]: check for check in report["checks"]}
            self.assertEqual(checks["preferences"]["status"], "pass")
            self.assertIn("fingerprint", checks["package"]["detail"])
            self.assertIn(checks["pdf browser"]["status"], {"pass", "info"})

    def test_pdf_refuses_missing_explicit_browser_without_downloading(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            html_path = root / "report.html"
            html_path.write_text("<!doctype html><title>Report</title>", encoding="utf-8")
            with self.assertRaisesRegex(readiness.AssessmentError, "executable not found"):
                readiness.render_pdf(html_path, root / "report.pdf", root / "missing-browser")

    def test_pdf_accepts_completed_artifact_when_browser_lingers(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            browser = root / "browser"
            browser.write_text("fixture\n", encoding="utf-8")
            browser.chmod(0o755)
            html_path = root / "report.html"
            pdf_path = root / "report.pdf"
            html_path.write_text("<!doctype html><title>Report</title>", encoding="utf-8")

            def linger(arguments: list[str], **_kwargs: object) -> None:
                target = next(value.split("=", 1)[1] for value in arguments if value.startswith("--print-to-pdf="))
                Path(target).write_bytes(b"%PDF-1.4\n" + b"x" * 2048)
                raise subprocess.TimeoutExpired(arguments, 60)

            with mock.patch.object(readiness.subprocess, "run", side_effect=linger):
                result = readiness.render_pdf(html_path, pdf_path, browser)

            self.assertEqual(result, pdf_path.resolve())
            self.assertGreater(result.stat().st_size, 1000)

    def test_vendor_is_dry_run_by_default_and_apply_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / ".agents" / "skills" / "agent-readiness"
            dry_run = run_cli("vendor", "--target", str(target), "--json")
            self.assertEqual(dry_run.returncode, 0, dry_run.stderr)
            self.assertFalse(target.exists())
            self.assertEqual(json.loads(dry_run.stdout)["mode"], "dry-run")

            applied = run_cli("vendor", "--target", str(target), "--apply", "--json")
            self.assertEqual(applied.returncode, 0, applied.stderr)
            self.assertTrue((target / readiness.VENDOR_METADATA).is_file())
            self.assertTrue((target / "scripts" / "readiness.py").is_file())
            (target / "LOCAL_NOTES.md").write_text("preserve me\n", encoding="utf-8")

            current = run_cli("vendor", "--target", str(target), "--apply", "--json")
            self.assertEqual(current.returncode, 0, current.stderr)
            plan = json.loads(current.stdout)
            self.assertTrue(all(item["status"] == "current" for item in plan["files"]))
            self.assertEqual((target / "LOCAL_NOTES.md").read_text(encoding="utf-8"), "preserve me\n")

            vendored_doctor = run_command(sys.executable, str(target / "scripts" / "readiness.py"), "doctor", "--json")
            self.assertEqual(vendored_doctor.returncode, 0, vendored_doctor.stderr)
            self.assertTrue(json.loads(vendored_doctor.stdout)["ok"])


if __name__ == "__main__":
    unittest.main()
