#!/usr/bin/env python3
"""Prepare and grade maintainer-facing behavioral evaluations for the readiness skill."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any


SKILL_ROOT = Path(__file__).resolve().parent.parent
SCENARIOS_PATH = SKILL_ROOT / "evals" / "scenarios.json"


class EvalError(ValueError):
    pass


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise EvalError(f"File not found: {path}") from error
    except json.JSONDecodeError as error:
        raise EvalError(f"Invalid JSON in {path}: {error}") from error
    if not isinstance(value, dict):
        raise EvalError(f"Expected a JSON object in {path}.")
    return value


def scenarios() -> dict[str, dict[str, Any]]:
    payload = read_json(SCENARIOS_PATH)
    values = payload.get("scenarios")
    if not isinstance(values, list):
        raise EvalError("Scenario catalog must contain a scenarios array.")
    result = {scenario.get("id"): scenario for scenario in values if isinstance(scenario, dict)}
    if len(result) != len(values) or any(not isinstance(key, str) or not key for key in result):
        raise EvalError("Every scenario requires a unique non-empty ID.")
    return result


def run(*arguments: str, cwd: Path | None = None) -> str:
    result = subprocess.run(arguments, cwd=cwd, check=True, capture_output=True, text=True)
    return result.stdout.strip()


def prepare(scenario: dict[str, Any], output: Path) -> Path:
    root = output.resolve()
    if root.exists():
        raise EvalError(f"Refusing to overwrite evaluation workspace: {root}")
    repository = root / "repo"
    repository.mkdir(parents=True)
    files = scenario.get("files")
    if not isinstance(files, dict) or not files:
        raise EvalError("Scenario requires fixture files.")
    for relative, content in files.items():
        if not isinstance(relative, str) or not isinstance(content, str):
            raise EvalError("Scenario fixture paths and contents must be strings.")
        destination = repository / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(content, encoding="utf-8")
    run("git", "init", "-b", "master", cwd=repository)
    run("git", "config", "user.email", "agent-eval@example.com", cwd=repository)
    run("git", "config", "user.name", "Agent Eval", cwd=repository)
    run("git", "add", "--all", cwd=repository)
    run("git", "commit", "-m", "Create isolated evaluation fixture", cwd=repository)
    baseline = {
        "commit": run("git", "rev-parse", "HEAD", cwd=repository),
        "status": run("git", "status", "--porcelain", cwd=repository),
    }
    (root / ".baseline.json").write_text(json.dumps(baseline, indent=2) + "\n", encoding="utf-8")
    prompt = (
        f"# Evaluation request\n\nRepository: `{repository}`\n\n{scenario['request']}\n\n"
        "After completing the task, copy the adjacent `agent-run.template.json` to `agent-run.json` "
        "outside the repository and fill it with the discovered applications, actual preference "
        "source, requested judgments, and requested action decisions.\n"
    )
    (root / "PROMPT.md").write_text(prompt, encoding="utf-8")
    template = {
        "applications": [],
        "preferences_source": "",
        "judgments": {
            key: {"status": "", "evidence": []}
            for key in scenario.get("required_judgments", {})
        },
        "actions": {key: "" for key in scenario.get("required_actions", {})},
    }
    (root / "agent-run.template.json").write_text(json.dumps(template, indent=2) + "\n", encoding="utf-8")
    return root


def grade(scenario: dict[str, Any], workspace: Path, artifact: dict[str, Any]) -> dict[str, Any]:
    root = workspace.resolve()
    repository = root / "repo"
    baseline = read_json(root / ".baseline.json")
    checks: list[dict[str, Any]] = []

    def check(name: str, passed: bool, detail: str) -> None:
        checks.append({"name": name, "passed": passed, "detail": detail})

    if scenario.get("read_only"):
        current_commit = run("git", "rev-parse", "HEAD", cwd=repository)
        current_status = run("git", "status", "--porcelain", cwd=repository)
        check("read-only commit", current_commit == baseline.get("commit"), f"baseline={baseline.get('commit')} current={current_commit}")
        check("read-only tree", current_status == baseline.get("status"), f"status={current_status or 'clean'}")
    actual_apps = sorted(artifact.get("applications", [])) if isinstance(artifact.get("applications"), list) else []
    if "expected_applications" in scenario:
        expected_apps = sorted(scenario["expected_applications"])
        apps_passed = actual_apps == expected_apps
        apps_detail = f"expected={expected_apps} actual={actual_apps}"
    else:
        expected_count = scenario.get("expected_application_count")
        apps_passed = len(actual_apps) == expected_count and len(set(actual_apps)) == len(actual_apps)
        apps_detail = f"expected_count={expected_count} actual={actual_apps}"
    check("application discovery", apps_passed, apps_detail)
    expected_preferences = scenario.get("expected_preferences_source")
    actual_preferences = artifact.get("preferences_source")
    normalized_preferences = Path(actual_preferences).name if isinstance(actual_preferences, str) else actual_preferences
    check(
        "preference precedence",
        normalized_preferences == expected_preferences,
        f"expected={expected_preferences} actual={actual_preferences}",
    )
    judgments = artifact.get("judgments", {})
    if not isinstance(judgments, dict):
        judgments = {}
    for key, expected_status in scenario.get("required_judgments", {}).items():
        value = judgments.get(key, {})
        status = value.get("status") if isinstance(value, dict) else None
        evidence = value.get("evidence") if isinstance(value, dict) else None
        passed = status == expected_status and isinstance(evidence, list) and bool(evidence)
        check(f"judgment {key}", passed, f"expected={expected_status} actual={status} evidence={bool(evidence)}")
    actions = artifact.get("actions", {})
    if not isinstance(actions, dict):
        actions = {}
    for action_id, expected_decision in scenario.get("required_actions", {}).items():
        actual_decision = actions.get(action_id)
        check(f"action {action_id}", actual_decision == expected_decision, f"expected={expected_decision} actual={actual_decision}")
    passed_count = sum(item["passed"] for item in checks)
    return {
        "scenario": scenario["id"],
        "passed": passed_count == len(checks),
        "score": passed_count / len(checks) if checks else 0.0,
        "passed_checks": passed_count,
        "total_checks": len(checks),
        "checks": checks,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("list", help="List behavioral evaluation scenarios.")
    prepare_parser = subparsers.add_parser("prepare", help="Create an isolated synthetic repository and prompt.")
    prepare_parser.add_argument("--scenario", required=True)
    prepare_parser.add_argument("--output", type=Path, required=True)
    grade_parser = subparsers.add_parser("grade", help="Grade an agent-run artifact and live workspace integrity.")
    grade_parser.add_argument("--scenario", required=True)
    grade_parser.add_argument("--workspace", type=Path, required=True)
    grade_parser.add_argument("--artifact", type=Path, required=True)
    return parser


def main() -> int:
    arguments = build_parser().parse_args()
    try:
        catalog = scenarios()
        if arguments.command == "list":
            for scenario in catalog.values():
                print(f"{scenario['id']}\t{scenario['description']}")
            return 0
        if arguments.scenario not in catalog:
            raise EvalError(f"Unknown scenario: {arguments.scenario}")
        scenario = catalog[arguments.scenario]
        if arguments.command == "prepare":
            root = prepare(scenario, arguments.output)
            print(f"Prepared: {root}")
            print(f"Prompt: {root / 'PROMPT.md'}")
            return 0
        result = grade(scenario, arguments.workspace, read_json(arguments.artifact))
        print(json.dumps(result, indent=2))
        return 0 if result["passed"] else 1
    except (EvalError, subprocess.CalledProcessError) as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
