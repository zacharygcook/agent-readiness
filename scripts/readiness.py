#!/usr/bin/env python3
"""Validate and render personally owned agent-readiness assessments."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SKILL_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_RUBRIC = SKILL_ROOT / "references" / "rubric.json"
PREFERENCES_TEMPLATE = SKILL_ROOT / "assets" / "AGENT_READINESS_PREFERENCES.md"
ALLOWED_STATUSES = {"pass", "fail", "not_applicable"}
ALLOWED_CONFIDENCE = {"high", "medium", "low"}
CATEGORY_TITLES = {
    "style_validation": "Style & Validation",
    "build_system": "Build System",
    "agent_workflow": "Agent Workflow",
    "testing": "Testing",
    "documentation": "Documentation",
    "dev_environment": "Development Environment",
    "observability": "Debugging & Observability",
    "security": "Security",
    "project_management": "Project Management",
}


class AssessmentError(ValueError):
    pass


@dataclass(frozen=True)
class CriterionScore:
    criterion_id: str
    owned_ratio: float | None
    compatibility_ratio: float | None
    owned_numerator: int | None
    owned_denominator: int
    compatibility_numerator: int | None
    compatibility_denominator: int
    failing_units: tuple[str, ...]
    skipped_units: tuple[str, ...]


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise AssessmentError(f"File not found: {path}") from error
    except json.JSONDecodeError as error:
        raise AssessmentError(f"Invalid JSON in {path}: {error}") from error
    if not isinstance(value, dict):
        raise AssessmentError(f"Expected a JSON object in {path}.")
    return value


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.write_text(f"{json.dumps(value, indent=2, sort_keys=False)}\n", encoding="utf-8")


def run_git(repo: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *arguments],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def load_rubric(path: Path) -> dict[str, Any]:
    rubric = read_json(path)
    criteria = rubric.get("criteria")
    if not isinstance(criteria, list):
        raise AssessmentError("Rubric must contain a criteria array.")
    ids = [criterion.get("id") for criterion in criteria if isinstance(criterion, dict)]
    if len(criteria) != 82 or len(set(ids)) != 82:
        raise AssessmentError(
            f"Rubric must contain exactly 82 unique criteria; found {len(criteria)} entries and {len(set(ids))} IDs."
        )
    return rubric


def parse_application(value: str) -> tuple[str, str]:
    if "=" not in value:
        raise argparse.ArgumentTypeError("Applications must use ID=PATH.")
    app_id, app_path = value.split("=", 1)
    if not app_id.strip() or not app_path.strip():
        raise argparse.ArgumentTypeError("Applications must use non-empty ID=PATH values.")
    return app_id.strip(), app_path.strip()


def create_skeleton(repo: Path, applications: list[tuple[str, str]], rubric: dict[str, Any]) -> dict[str, Any]:
    if not applications:
        raise AssessmentError("Provide at least one --app ID=PATH.")
    app_map = {
        app_id: {"path": app_path, "description": "TODO: describe this application"}
        for app_id, app_path in applications
    }
    if len(app_map) != len(applications):
        raise AssessmentError("Application IDs must be unique.")

    criteria: dict[str, Any] = {}
    for criterion in rubric["criteria"]:
        empty_judgment = {
            "status": "unscored",
            "rationale": "",
            "evidence": [],
            "confidence": "low",
        }
        if criterion["scope"] == "repository":
            criteria[criterion["id"]] = empty_judgment
        else:
            criteria[criterion["id"]] = {
                "applications": {
                    app_id: dict(empty_judgment) for app_id in app_map
                }
            }

    preference_path = repo / "AGENT_READINESS_PREFERENCES.md"
    return {
        "schema_version": "1.0",
        "rubric_version": rubric["version"],
        "repository": {
            "name": repo.name,
            "path": str(repo.resolve()),
            "commit": run_git(repo, "rev-parse", "HEAD"),
            "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "dirty": bool(run_git(repo, "status", "--porcelain")),
            "applications": app_map,
        },
        "preferences": {
            "source": (
                str(preference_path.relative_to(repo))
                if preference_path.exists()
                else "skill defaults"
            ),
            "overrides": [],
        },
        "criteria": criteria,
    }


def validate_judgment(
    judgment: Any,
    *,
    criterion_id: str,
    unit: str,
    skippable: bool,
) -> list[str]:
    errors: list[str] = []
    label = f"{criterion_id} ({unit})"
    if not isinstance(judgment, dict):
        return [f"{label}: judgment must be an object."]
    status = judgment.get("status")
    if status not in ALLOWED_STATUSES:
        errors.append(f"{label}: status must be pass, fail, or not_applicable.")
    if status == "not_applicable" and not skippable:
        errors.append(f"{label}: non-skippable criterion cannot be not_applicable.")
    rationale = judgment.get("rationale")
    if not isinstance(rationale, str) or not rationale.strip():
        errors.append(f"{label}: rationale is required.")
    elif len(rationale) > 500:
        errors.append(f"{label}: rationale exceeds 500 characters.")
    evidence = judgment.get("evidence")
    if not isinstance(evidence, list) or not evidence:
        errors.append(f"{label}: at least one evidence item is required.")
    elif any(not isinstance(item, str) or not item.strip() for item in evidence):
        errors.append(f"{label}: evidence items must be non-empty strings.")
    confidence = judgment.get("confidence")
    if confidence not in ALLOWED_CONFIDENCE:
        errors.append(f"{label}: confidence must be high, medium, or low.")
    return errors


def validate_assessment(assessment: dict[str, Any], rubric: dict[str, Any]) -> None:
    errors: list[str] = []
    repository = assessment.get("repository")
    if not isinstance(repository, dict):
        raise AssessmentError("Assessment repository must be an object.")
    applications = repository.get("applications")
    if not isinstance(applications, dict) or not applications:
        errors.append("repository.applications must contain at least one application.")
        applications = {}
    app_ids = set(applications)

    entries = assessment.get("criteria")
    if not isinstance(entries, dict):
        errors.append("criteria must be an object.")
        entries = {}
    rubric_by_id = {criterion["id"]: criterion for criterion in rubric["criteria"]}
    missing = sorted(set(rubric_by_id) - set(entries))
    extra = sorted(set(entries) - set(rubric_by_id))
    if missing:
        errors.append(f"Missing criteria: {', '.join(missing)}")
    if extra:
        errors.append(f"Unknown criteria: {', '.join(extra)}")

    for criterion_id, definition in rubric_by_id.items():
        if criterion_id not in entries:
            continue
        entry = entries[criterion_id]
        if definition["scope"] == "repository":
            errors.extend(
                validate_judgment(
                    entry,
                    criterion_id=criterion_id,
                    unit="repository",
                    skippable=definition["skippable"],
                )
            )
            continue
        if not isinstance(entry, dict) or not isinstance(entry.get("applications"), dict):
            errors.append(f"{criterion_id}: application-scoped entry requires applications object.")
            continue
        judgments = entry["applications"]
        judgment_ids = set(judgments)
        if judgment_ids != app_ids:
            absent = sorted(app_ids - judgment_ids)
            unexpected = sorted(judgment_ids - app_ids)
            if absent:
                errors.append(f"{criterion_id}: missing apps: {', '.join(absent)}")
            if unexpected:
                errors.append(f"{criterion_id}: unknown apps: {', '.join(unexpected)}")
        for app_id in sorted(app_ids & judgment_ids):
            errors.extend(
                validate_judgment(
                    judgments[app_id],
                    criterion_id=criterion_id,
                    unit=app_id,
                    skippable=definition["skippable"],
                )
            )

    if errors:
        raise AssessmentError("Assessment validation failed:\n- " + "\n- ".join(errors))


def score_criterion(
    definition: dict[str, Any],
    entry: dict[str, Any],
    app_ids: tuple[str, ...],
) -> CriterionScore:
    criterion_id = definition["id"]
    if definition["scope"] == "repository":
        status = entry["status"]
        ratio = None if status == "not_applicable" else float(status == "pass")
        return CriterionScore(
            criterion_id=criterion_id,
            owned_ratio=ratio,
            compatibility_ratio=ratio,
            owned_numerator=None if ratio is None else int(ratio),
            owned_denominator=1,
            compatibility_numerator=None if ratio is None else int(ratio),
            compatibility_denominator=1,
            failing_units=("repository",) if status == "fail" else (),
            skipped_units=("repository",) if status == "not_applicable" else (),
        )

    judgments = entry["applications"]
    statuses = {app_id: judgments[app_id]["status"] for app_id in app_ids}
    applicable = [app_id for app_id, status in statuses.items() if status != "not_applicable"]
    passing = [app_id for app_id, status in statuses.items() if status == "pass"]
    failing = tuple(app_id for app_id, status in statuses.items() if status == "fail")
    skipped = tuple(app_id for app_id, status in statuses.items() if status == "not_applicable")
    if not applicable:
        owned_ratio = None
        compatibility_ratio = None
        owned_numerator = None
        compatibility_numerator = None
    else:
        owned_numerator = len(passing)
        compatibility_numerator = len(passing)
        owned_ratio = len(passing) / len(applicable)
        compatibility_ratio = len(passing) / len(app_ids)
    return CriterionScore(
        criterion_id=criterion_id,
        owned_ratio=owned_ratio,
        compatibility_ratio=compatibility_ratio,
        owned_numerator=owned_numerator,
        owned_denominator=max(1, len(applicable)),
        compatibility_numerator=compatibility_numerator,
        compatibility_denominator=len(app_ids),
        failing_units=failing,
        skipped_units=skipped,
    )


def overall_percentage(scores: list[CriterionScore], attribute: str) -> float:
    values = [getattr(score, attribute) for score in scores]
    applicable = [value for value in values if value is not None]
    if not applicable:
        return 0.0
    return sum(applicable) * 100 / len(applicable)


def readiness_level(percentage: float) -> int:
    if percentage < 20:
        return 1
    if percentage < 40:
        return 2
    if percentage < 60:
        return 3
    if percentage < 80:
        return 4
    return 5


def judgment_summary(definition: dict[str, Any], entry: dict[str, Any], score: CriterionScore) -> str:
    if score.owned_ratio is None:
        return "Skipped"
    if definition["scope"] == "repository":
        return "Pass" if score.owned_ratio == 1 else "Fail"
    return f"{score.owned_numerator}/{score.owned_denominator} applicable apps"


def render_markdown(
    assessment: dict[str, Any],
    rubric: dict[str, Any],
    scores: list[CriterionScore],
) -> str:
    definitions = {criterion["id"]: criterion for criterion in rubric["criteria"]}
    entries = assessment["criteria"]
    owned_percentage = overall_percentage(scores, "owned_ratio")
    compatibility_percentage = overall_percentage(scores, "compatibility_ratio")
    owned_level = readiness_level(owned_percentage)
    compatibility_level = readiness_level(compatibility_percentage)
    score_by_id = {score.criterion_id: score for score in scores}
    applicable_count = sum(score.owned_ratio is not None for score in scores)
    skipped_count = len(scores) - applicable_count

    lines = [
        "# Agent Readiness Report",
        "",
        f"Generated: {assessment['repository']['generated_at']}",
        f"Commit: `{assessment['repository']['commit']}`",
        f"Preferences: `{assessment.get('preferences', {}).get('source', 'skill defaults')}`",
        "",
        "## Score",
        "",
        f"- **Owned readiness:** Level {owned_level}, **{owned_percentage:.2f}%**",
        f"- **Compatibility view:** Level {compatibility_level}, **{compatibility_percentage:.2f}%**",
        f"- Applicable criteria: {applicable_count}; fully skipped: {skipped_count}; rubric: {rubric['version']}",
        "",
        "The owned score excludes inapplicable applications from each criterion denominator. The",
        "compatibility view reproduces the legacy behavior where a mixed inapplicable application",
        "reduces an app-scoped criterion score.",
        "",
        "## Applications",
        "",
    ]
    for app_id, application in assessment["repository"]["applications"].items():
        lines.append(f"- `{app_id}` (`{application['path']}`): {application['description']}")

    category_scores: dict[str, list[float]] = defaultdict(list)
    for score in scores:
        if score.owned_ratio is not None:
            category_scores[definitions[score.criterion_id]["category"]].append(score.owned_ratio)
    lines.extend(["", "## Category Summary", "", "| Category | Score |", "|---|---:|"])
    for category, title in CATEGORY_TITLES.items():
        values = category_scores.get(category, [])
        percentage = sum(values) * 100 / len(values) if values else 0.0
        lines.append(f"| {title} | {percentage:.2f}% |")

    failures = [
        score
        for score in scores
        if score.owned_ratio is not None and score.owned_ratio < 1
    ]
    failures.sort(key=lambda score: (definitions[score.criterion_id]["level"], definitions[score.criterion_id]["title"]))
    lines.extend(["", "## Failing Criteria", ""])
    if not failures:
        lines.append("- None. All applicable criteria pass.")
    else:
        for score in failures:
            definition = definitions[score.criterion_id]
            units = ", ".join(score.failing_units)
            lines.append(
                f"- **{definition['title']}** (`{definition['id']}`, Level {definition['level']}) — "
                f"failing: {units}. {definition['guidance']}"
            )

    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for definition in rubric["criteria"]:
        grouped[definition["category"]].append(definition)
    lines.extend(["", "## Criteria", ""])
    for category, title in CATEGORY_TITLES.items():
        definitions_in_category = grouped.get(category, [])
        if not definitions_in_category:
            continue
        lines.extend([f"### {title}", "", "| Criterion | Level | Owned | Compatibility |", "|---|---:|---:|---:|"])
        for definition in definitions_in_category:
            score = score_by_id[definition["id"]]
            owned = (
                "Skipped"
                if score.owned_ratio is None
                else f"{score.owned_ratio * 100:.0f}%"
            )
            compatibility = (
                "Skipped"
                if score.compatibility_ratio is None
                else f"{score.compatibility_ratio * 100:.0f}%"
            )
            lines.append(
                f"| {definition['title']} (`{definition['id']}`) | {definition['level']} | {owned} | {compatibility} |"
            )
        lines.append("")

    lines.extend(["## Evidence & Rationale", ""])
    for definition in rubric["criteria"]:
        entry = entries[definition["id"]]
        score = score_by_id[definition["id"]]
        lines.append(f"### {definition['title']} — {judgment_summary(definition, entry, score)}")
        lines.append("")
        if definition["scope"] == "repository":
            lines.append(f"{entry['rationale']} Confidence: {entry['confidence']}.")
            lines.extend(f"- {item}" for item in entry["evidence"])
        else:
            for app_id, judgment in entry["applications"].items():
                lines.append(
                    f"- **{app_id} — {judgment['status']} ({judgment['confidence']}):** {judgment['rationale']}"
                )
                lines.extend(f"  - {item}" for item in judgment["evidence"])
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def report_payload(
    assessment: dict[str, Any],
    rubric: dict[str, Any],
    scores: list[CriterionScore],
) -> dict[str, Any]:
    definitions = {criterion["id"]: criterion for criterion in rubric["criteria"]}
    owned_percentage = overall_percentage(scores, "owned_ratio")
    compatibility_percentage = overall_percentage(scores, "compatibility_ratio")
    return {
        "schema_version": "1.0",
        "rubric_version": rubric["version"],
        "repository": assessment["repository"],
        "preferences": assessment.get("preferences", {}),
        "summary": {
            "owned_percentage": round(owned_percentage, 4),
            "owned_level": readiness_level(owned_percentage),
            "compatibility_percentage": round(compatibility_percentage, 4),
            "compatibility_level": readiness_level(compatibility_percentage),
            "applicable_criteria": sum(score.owned_ratio is not None for score in scores),
            "skipped_criteria": sum(score.owned_ratio is None for score in scores),
        },
        "criteria": {
            score.criterion_id: {
                "title": definitions[score.criterion_id]["title"],
                "level": definitions[score.criterion_id]["level"],
                "scope": definitions[score.criterion_id]["scope"],
                "owned": {
                    "ratio": score.owned_ratio,
                    "numerator": score.owned_numerator,
                    "denominator": score.owned_denominator,
                },
                "compatibility": {
                    "ratio": score.compatibility_ratio,
                    "numerator": score.compatibility_numerator,
                    "denominator": score.compatibility_denominator,
                },
                "failing_units": list(score.failing_units),
                "skipped_units": list(score.skipped_units),
                "assessment": assessment["criteria"][score.criterion_id],
            }
            for score in scores
        },
    }


def score_assessment(assessment: dict[str, Any], rubric: dict[str, Any]) -> list[CriterionScore]:
    validate_assessment(assessment, rubric)
    app_ids = tuple(assessment["repository"]["applications"])
    return [
        score_criterion(
            definition,
            assessment["criteria"][definition["id"]],
            app_ids,
        )
        for definition in rubric["criteria"]
    ]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rubric", type=Path, default=DEFAULT_RUBRIC)
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init", help="Create an unscored assessment skeleton.")
    init_parser.add_argument("--repo", type=Path, required=True)
    init_parser.add_argument("--app", action="append", type=parse_application, default=[])
    init_parser.add_argument("--output", type=Path, required=True)

    validate_parser = subparsers.add_parser("validate", help="Validate a completed assessment.")
    validate_parser.add_argument("--assessment", type=Path, required=True)

    score_parser = subparsers.add_parser("score", help="Validate, score, and render a report.")
    score_parser.add_argument("--assessment", type=Path, required=True)
    score_parser.add_argument("--output-dir", type=Path, required=True)

    subparsers.add_parser("list", help="List rubric criteria.")

    preferences_parser = subparsers.add_parser("preferences", help="Copy the preferences template.")
    preferences_parser.add_argument("--output", type=Path, required=True)
    return parser


def main() -> int:
    parser = build_parser()
    arguments = parser.parse_args()
    try:
        rubric = load_rubric(arguments.rubric)
        if arguments.command == "init":
            output = arguments.output.resolve()
            if output.exists():
                raise AssessmentError(f"Refusing to overwrite existing file: {output}")
            output.parent.mkdir(parents=True, exist_ok=True)
            write_json(output, create_skeleton(arguments.repo.resolve(), arguments.app, rubric))
            print(f"Created unscored assessment: {output}")
        elif arguments.command == "validate":
            validate_assessment(read_json(arguments.assessment), rubric)
            print("Assessment is valid: 82 criteria with complete evidence-backed judgments.")
        elif arguments.command == "score":
            assessment = read_json(arguments.assessment)
            scores = score_assessment(assessment, rubric)
            output_dir = arguments.output_dir.resolve()
            output_dir.mkdir(parents=True, exist_ok=True)
            markdown_path = output_dir / "agent-readiness-report.md"
            json_path = output_dir / "agent-readiness-report.json"
            markdown_path.write_text(render_markdown(assessment, rubric, scores), encoding="utf-8")
            payload = report_payload(assessment, rubric, scores)
            write_json(json_path, payload)
            print(
                f"Owned: Level {payload['summary']['owned_level']} "
                f"({payload['summary']['owned_percentage']:.2f}%). "
                f"Compatibility: Level {payload['summary']['compatibility_level']} "
                f"({payload['summary']['compatibility_percentage']:.2f}%)."
            )
            print(f"Markdown: {markdown_path}")
            print(f"JSON: {json_path}")
        elif arguments.command == "list":
            for criterion in rubric["criteria"]:
                skip = "yes" if criterion["skippable"] else "no"
                print(
                    f"{criterion['id']}\t{criterion['scope']}\tL{criterion['level']}\t"
                    f"skippable={skip}\t{criterion['title']}"
                )
        elif arguments.command == "preferences":
            output = arguments.output.resolve()
            if output.exists():
                raise AssessmentError(f"Refusing to overwrite existing file: {output}")
            output.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(PREFERENCES_TEMPLATE, output)
            print(f"Created preferences: {output}")
    except (AssessmentError, subprocess.CalledProcessError) as error:
        print(str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
