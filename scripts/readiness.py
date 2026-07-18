#!/usr/bin/env python3
"""Validate and render personally owned agent-readiness assessments."""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import shutil
import subprocess
import sys
import tempfile
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SKILL_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_RUBRIC = SKILL_ROOT / "references" / "rubric.json"
PREFERENCES_TEMPLATE = SKILL_ROOT / "assets" / "DEFAULT_AGENT_READINESS_PREFERENCES.md"
VERSION_FILE = SKILL_ROOT / "VERSION"
VENDOR_METADATA = ".agent-readiness-package.json"
CORE_DISTRIBUTABLE_FILES = (
    "VERSION",
    "SKILL.md",
    "agents/openai.yaml",
    "assets/DEFAULT_AGENT_READINESS_PREFERENCES.md",
    "references/assessment-format.md",
    "references/report-workflow.md",
    "references/remediation-loop.md",
    "references/rubric.json",
    "scripts/readiness.py",
)
ALLOWED_STATUSES = {"pass", "fail", "not_applicable"}
ALLOWED_CONFIDENCE = {"high", "medium", "low"}
ALLOWED_EFFORT = {"small", "medium", "large"}
ALLOWED_AUTHORITY = {"autonomous", "approval_required", "deferred"}
ALLOWED_RECOMMENDATION_FLAGS = {
    "external_account",
    "large_refactor",
    "paid_service",
    "production_change",
}
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
OWNED_EXTENSION_DEFINITIONS = {
    "build_deploy_performance_hygiene": {
        "version": "1.0",
        "title": "Build & deploy performance hygiene",
        "controls": {
            "phase_timing": "Measured build and deploy phase timing",
            "service_triggers": "Service-specific trigger boundaries",
            "artifact_boundaries": "Runtime artifact boundaries",
            "cache_limits": "Enforced cache limits",
            "regression_budgets": "Regression budgets",
        },
    },
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


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    try:
        return sha256_bytes(path.read_bytes())
    except FileNotFoundError as error:
        raise AssessmentError(f"File not found: {path}") from error


def package_version() -> str:
    try:
        value = VERSION_FILE.read_text(encoding="utf-8").strip()
    except FileNotFoundError as error:
        raise AssessmentError(f"Missing package version: {VERSION_FILE}") from error
    if not value or any(character.isspace() for character in value):
        raise AssessmentError("VERSION must contain one non-empty, whitespace-free value.")
    return value


def distributable_files() -> tuple[str, ...]:
    return CORE_DISTRIBUTABLE_FILES


def package_file_checksums(root: Path = SKILL_ROOT) -> dict[str, str]:
    checksums: dict[str, str] = {}
    for relative in distributable_files():
        path = root / relative
        if not path.is_file():
            raise AssessmentError(f"Missing distributable skill file: {relative}")
        checksums[relative] = sha256_file(path)
    return checksums


def package_fingerprint(root: Path = SKILL_ROOT) -> str:
    checksums = package_file_checksums(root)
    material = "".join(f"{relative}\0{checksums[relative]}\n" for relative in sorted(checksums))
    return sha256_bytes(material.encode("utf-8"))


def safe_git(repo: Path, *arguments: str) -> str | None:
    try:
        return run_git(repo, *arguments)
    except (FileNotFoundError, subprocess.CalledProcessError):
        return None


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
    string_ids = [criterion_id for criterion_id in ids if isinstance(criterion_id, str) and criterion_id]
    if len(criteria) != 82 or len(ids) != 82 or len(string_ids) != 82 or len(set(string_ids)) != 82:
        raise AssessmentError(
            f"Rubric must contain exactly 82 unique criteria; found {len(criteria)} entries and {len(set(string_ids))} IDs."
        )
    errors: list[str] = []
    for index, criterion in enumerate(criteria):
        if not isinstance(criterion, dict):
            errors.append(f"criteria[{index}] must be an object")
            continue
        criterion_id = criterion.get("id")
        label = criterion_id if isinstance(criterion_id, str) and criterion_id else f"criteria[{index}]"
        if not isinstance(criterion_id, str) or not criterion_id.strip():
            errors.append(f"{label}: id must be a non-empty string")
        if criterion.get("scope") not in {"repository", "application"}:
            errors.append(f"{label}: scope must be repository or application")
        if criterion.get("category") not in CATEGORY_TITLES:
            errors.append(f"{label}: category is not recognized")
        level = criterion.get("level")
        if isinstance(level, bool) or not isinstance(level, int) or not 1 <= level <= 5:
            errors.append(f"{label}: level must be an integer from 1 through 5")
        if not isinstance(criterion.get("skippable"), bool):
            errors.append(f"{label}: skippable must be a boolean")
        for field in ("title", "guidance"):
            value = criterion.get(field)
            if not isinstance(value, str) or not value.strip():
                errors.append(f"{label}: {field} must be a non-empty string")
    if not isinstance(rubric.get("version"), str) or not rubric["version"].strip():
        errors.append("version must be a non-empty string")
    if errors:
        raise AssessmentError("Rubric validation failed:\n- " + "\n- ".join(errors))
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
    preference_source = preference_path if preference_path.exists() else PREFERENCES_TEMPLATE
    generated_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    return {
        "schema_version": "1.0",
        "rubric_version": rubric["version"],
        "repository": {
            "name": repo.name,
            "path": str(repo.resolve()),
            "commit": run_git(repo, "rev-parse", "HEAD"),
            "generated_at": generated_at,
            "dirty": bool(run_git(repo, "status", "--porcelain")),
            "applications": app_map,
        },
        "preferences": {
            "source": (
                str(preference_path.relative_to(repo))
                if preference_path.exists()
                else "skill defaults"
            ),
            "checksum": sha256_file(preference_source),
            "overrides": [],
        },
        "provenance": {
            "audit_timestamp": generated_at,
            "rubric_checksum": sha256_bytes(
                json.dumps(rubric, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ),
            "preferences_checksum": sha256_file(preference_source),
            "skill_version": package_version(),
            "skill_fingerprint": package_fingerprint(),
            "skill_source_commit": safe_git(SKILL_ROOT, "rev-parse", "HEAD"),
            "repository_commit": run_git(repo, "rev-parse", "HEAD"),
            "repository_dirty": bool(run_git(repo, "status", "--porcelain")),
            "applications": list(app_map),
            "evidence_checks": [],
        },
        "recommendations": [],
        "owned_extensions": {},
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


def validate_provenance(value: Any) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, dict):
        return ["provenance must be an object when provided."]
    errors: list[str] = []
    for field in (
        "audit_timestamp",
        "rubric_checksum",
        "preferences_checksum",
        "skill_version",
        "skill_fingerprint",
        "repository_commit",
    ):
        field_value = value.get(field)
        if not isinstance(field_value, str) or not field_value.strip():
            errors.append(f"provenance.{field} must be a non-empty string.")
    if not isinstance(value.get("repository_dirty"), bool):
        errors.append("provenance.repository_dirty must be a boolean.")
    applications = value.get("applications")
    if not isinstance(applications, list) or any(
        not isinstance(application, str) or not application for application in applications
    ):
        errors.append("provenance.applications must be an array of non-empty IDs.")
    checks = value.get("evidence_checks")
    if not isinstance(checks, list):
        errors.append("provenance.evidence_checks must be an array.")
        return errors
    for index, check in enumerate(checks):
        label = f"provenance.evidence_checks[{index}]"
        if not isinstance(check, dict):
            errors.append(f"{label} must be an object.")
            continue
        if check.get("kind") not in {"command", "external"}:
            errors.append(f"{label}.kind must be command or external.")
        for field, maximum in (("label", 120), ("checked_at", 40), ("summary", 500)):
            field_value = check.get(field)
            if not isinstance(field_value, str) or not field_value.strip():
                errors.append(f"{label}.{field} must be a non-empty string.")
            elif len(field_value) > maximum:
                errors.append(f"{label}.{field} exceeds {maximum} characters.")
        exit_status = check.get("exit_status")
        if exit_status is not None and (isinstance(exit_status, bool) or not isinstance(exit_status, int)):
            errors.append(f"{label}.exit_status must be an integer or null.")
        command = check.get("command")
        if command is not None and (
            not isinstance(command, str) or not command.strip() or len(command) > 300 or "\n" in command
        ):
            errors.append(f"{label}.command must be one non-empty line of at most 300 characters.")
        fresh_until = check.get("fresh_until")
        if fresh_until is not None and (not isinstance(fresh_until, str) or not fresh_until.strip()):
            errors.append(f"{label}.fresh_until must be a non-empty timestamp when provided.")
    return errors


def validate_recommendations(value: Any, criterion_ids: set[str]) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        return ["recommendations must be an array when provided."]
    errors: list[str] = []
    priorities: set[int] = set()
    recommended: set[str] = set()
    for index, recommendation in enumerate(value):
        label = f"recommendations[{index}]"
        if not isinstance(recommendation, dict):
            errors.append(f"{label} must be an object.")
            continue
        criterion_id = recommendation.get("criterion_id")
        if criterion_id not in criterion_ids:
            errors.append(f"{label}.criterion_id must identify a rubric criterion.")
        elif criterion_id in recommended:
            errors.append(f"{label}.criterion_id must not be duplicated.")
        else:
            recommended.add(criterion_id)
        priority = recommendation.get("priority")
        if isinstance(priority, bool) or not isinstance(priority, int) or priority < 1:
            errors.append(f"{label}.priority must be a positive integer.")
        elif priority in priorities:
            errors.append(f"{label}.priority must be unique.")
        else:
            priorities.add(priority)
        rationale = recommendation.get("rationale")
        if not isinstance(rationale, str) or not rationale.strip():
            errors.append(f"{label}.rationale must be a non-empty string.")
        elif len(rationale) > 500:
            errors.append(f"{label}.rationale exceeds 500 characters.")
        if recommendation.get("effort") not in ALLOWED_EFFORT:
            errors.append(f"{label}.effort must be small, medium, or large.")
        if recommendation.get("authority") not in ALLOWED_AUTHORITY:
            errors.append(f"{label}.authority must be autonomous, approval_required, or deferred.")
        flags = recommendation.get("flags", [])
        if not isinstance(flags, list) or any(flag not in ALLOWED_RECOMMENDATION_FLAGS for flag in flags):
            errors.append(
                f"{label}.flags may contain only: {', '.join(sorted(ALLOWED_RECOMMENDATION_FLAGS))}."
            )
    return errors


def validate_owned_extensions(value: Any) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, dict):
        return ["owned_extensions must be an object when provided."]
    errors: list[str] = []
    unknown = sorted(set(value) - set(OWNED_EXTENSION_DEFINITIONS))
    if unknown:
        errors.append(f"Unknown owned extensions: {', '.join(unknown)}")
    for extension_id, definition in OWNED_EXTENSION_DEFINITIONS.items():
        entry = value.get(extension_id)
        if entry is None:
            continue
        if not isinstance(entry, dict):
            errors.append(f"owned_extensions.{extension_id} must be an object.")
            continue
        if entry.get("version") != definition["version"]:
            errors.append(
                f"owned_extensions.{extension_id}.version must be {definition['version']}."
            )
        controls = entry.get("controls")
        if not isinstance(controls, dict):
            errors.append(f"owned_extensions.{extension_id}.controls must be an object.")
            continue
        expected = set(definition["controls"])
        missing = sorted(expected - set(controls))
        extra = sorted(set(controls) - expected)
        if missing:
            errors.append(f"owned_extensions.{extension_id}: missing controls: {', '.join(missing)}")
        if extra:
            errors.append(f"owned_extensions.{extension_id}: unknown controls: {', '.join(extra)}")
        for control_id in sorted(expected & set(controls)):
            errors.extend(
                validate_judgment(
                    controls[control_id],
                    criterion_id=f"{extension_id}.{control_id}",
                    unit="repository",
                    skippable=False,
                )
            )
    return errors


def owned_extension_summary(assessment: dict[str, Any]) -> dict[str, Any]:
    entries = assessment.get("owned_extensions", {})
    if not isinstance(entries, dict):
        return {"checkpoints": [], "checkpoint_denominator": 0, "control_denominator": 0, "controls_passing": 0}
    checkpoints: list[dict[str, Any]] = []
    controls_passing = 0
    control_denominator = 0
    for extension_id, definition in OWNED_EXTENSION_DEFINITIONS.items():
        entry = entries.get(extension_id)
        if not isinstance(entry, dict):
            continue
        controls = entry.get("controls", {})
        if not isinstance(controls, dict):
            continue
        control_rows = []
        for control_id, title in definition["controls"].items():
            judgment = controls.get(control_id, {})
            status = judgment.get("status") if isinstance(judgment, dict) else "fail"
            control_rows.append({"id": control_id, "title": title, "status": status, "assessment": judgment})
            control_denominator += 1
            controls_passing += int(status == "pass")
        checkpoints.append({
            "id": extension_id,
            "title": definition["title"],
            "version": definition["version"],
            "status": "pass" if control_rows and all(row["status"] == "pass" for row in control_rows) else "fail",
            "controls": control_rows,
        })
    return {
        "checkpoints": checkpoints,
        "checkpoint_denominator": len(checkpoints),
        "checkpoints_passing": sum(checkpoint["status"] == "pass" for checkpoint in checkpoints),
        "control_denominator": control_denominator,
        "controls_passing": controls_passing,
    }


def validate_assessment(assessment: dict[str, Any], rubric: dict[str, Any]) -> None:
    errors: list[str] = []
    if assessment.get("schema_version") != "1.0":
        errors.append("schema_version must be 1.0.")
    if assessment.get("rubric_version") != rubric["version"]:
        errors.append(
            f"rubric_version must match the loaded rubric ({rubric['version']})."
        )
    repository = assessment.get("repository")
    if not isinstance(repository, dict):
        raise AssessmentError("Assessment repository must be an object.")
    applications = repository.get("applications")
    if not isinstance(applications, dict) or not applications:
        errors.append("repository.applications must contain at least one application.")
        applications = {}
    app_ids = set(applications)
    errors.extend(validate_provenance(assessment.get("provenance")))

    entries = assessment.get("criteria")
    if not isinstance(entries, dict):
        errors.append("criteria must be an object.")
        entries = {}
    rubric_by_id = {criterion["id"]: criterion for criterion in rubric["criteria"]}
    errors.extend(validate_recommendations(assessment.get("recommendations"), set(rubric_by_id)))
    errors.extend(validate_owned_extensions(assessment.get("owned_extensions")))
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
    progress: dict[str, Any] | None = None,
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
    ]
    extension_summary = owned_extension_summary(assessment)
    if extension_summary["checkpoint_denominator"]:
        lines.extend([
            "## Owned Extensions",
            "",
            "These checkpoint controls are reported separately and do not change the stable 82-criterion owned or compatibility scores.",
            "",
        ])
        for checkpoint in extension_summary["checkpoints"]:
            controls = checkpoint["controls"]
            passing = sum(control["status"] == "pass" for control in controls)
            lines.append(
                f"- **{checkpoint['title']}** (`{checkpoint['id']}` v{checkpoint['version']}) — "
                f"{checkpoint['status']}; {passing}/{len(controls)} controls passing."
            )
        lines.extend([
            f"- Checkpoint denominator: {extension_summary['checkpoint_denominator']}; control denominator: {extension_summary['control_denominator']}.",
            "",
        ])
    lines.extend(["## Applications", ""])
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

    actions, recommendations_ranked = next_actions(assessment, definitions, scores)
    lines.extend(["", "## Highest-Value Next Actions", ""])
    if not actions:
        lines.append("- None. All applicable criteria pass.")
    else:
        if not recommendations_ranked:
            lines.append(
                "_No agent-ranked recommendations were supplied; these candidates are ordered by "
                "level and score gap and still require repository-aware prioritization._"
            )
            lines.append("")
        for action in actions:
            flags = ", ".join(action["flags"]) if action["flags"] else "none"
            lines.append(
                f"{action['priority']}. **{action['title']}** (`{action['criterion_id']}`) — "
                f"effort: {action['effort']}; authority: {action['authority']}; flags: {flags}. "
                f"{action['rationale']}"
            )

    lines.extend(["", "## Progress Since Previous Round", ""])
    if progress is None:
        lines.append("- No previous assessment was supplied for comparison.")
    else:
        progress_summary = progress["summary"]
        lines.extend(
            [
                f"- Owned score delta: **{signed(progress_summary['owned_percentage_delta'])} points**",
                f"- Improvements: **{progress_summary['improvement_count']}**",
                f"- Regressions: **{progress_summary['regression_count']}**",
                f"- Changed criteria: **{progress_summary['changed_criteria']}**",
            ]
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


def ratio_state(ratio: float | None) -> str:
    if ratio is None:
        return "skipped"
    if ratio == 1:
        return "pass"
    if ratio == 0:
        return "fail"
    return "partial"


def score_breakdown(
    scores: list[CriterionScore],
    definitions: dict[str, dict[str, Any]],
) -> tuple[dict[str, int], dict[str, dict[str, float | int]]]:
    overall = {"pass": 0, "partial": 0, "fail": 0, "skipped": 0}
    category_values: dict[str, list[CriterionScore]] = defaultdict(list)
    for score in scores:
        overall[ratio_state(score.owned_ratio)] += 1
        category_values[definitions[score.criterion_id]["category"]].append(score)
    categories: dict[str, dict[str, float | int]] = {}
    for category in CATEGORY_TITLES:
        values = category_values.get(category, [])
        applicable = [score.owned_ratio for score in values if score.owned_ratio is not None]
        counts = {"pass": 0, "partial": 0, "fail": 0, "skipped": 0}
        for score in values:
            counts[ratio_state(score.owned_ratio)] += 1
        categories[category] = {
            "percentage": round(sum(applicable) * 100 / len(applicable), 4) if applicable else 0.0,
            "points_earned": round(sum(applicable), 4),
            "points_available": len(applicable),
            "total": len(values),
            **counts,
        }
    return overall, categories


def application_breakdown(assessment: dict[str, Any], rubric: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    app_definitions = [definition for definition in rubric["criteria"] if definition["scope"] == "application"]
    for app_id, application in assessment["repository"]["applications"].items():
        counts = {"pass": 0, "fail": 0, "not_applicable": 0}
        for definition in app_definitions:
            judgment = assessment["criteria"][definition["id"]]["applications"][app_id]
            counts[judgment["status"]] += 1
        applicable = counts["pass"] + counts["fail"]
        result[app_id] = {
            "path": application["path"],
            "description": application["description"],
            "percentage": round(counts["pass"] * 100 / applicable, 4) if applicable else 0.0,
            "applicable": applicable,
            **counts,
        }
    return result


def next_actions(
    assessment: dict[str, Any],
    definitions: dict[str, dict[str, Any]],
    scores: list[CriterionScore],
) -> tuple[list[dict[str, Any]], bool]:
    score_by_id = {score.criterion_id: score for score in scores}
    recommendations = assessment.get("recommendations", [])
    if recommendations:
        actions = []
        for recommendation in sorted(recommendations, key=lambda item: item["priority"]):
            criterion_id = recommendation["criterion_id"]
            score = score_by_id[criterion_id]
            definition = definitions[criterion_id]
            actions.append(
                {
                    **recommendation,
                    "title": definition["title"],
                    "level": definition["level"],
                    "current_percentage": None if score.owned_ratio is None else round(score.owned_ratio * 100, 2),
                    "failing_units": list(score.failing_units),
                    "source": "assessment",
                }
            )
        return actions, True
    failures = [
        score for score in scores
        if score.owned_ratio is not None and score.owned_ratio < 1
    ]
    failures.sort(
        key=lambda score: (
            definitions[score.criterion_id]["level"],
            score.owned_ratio if score.owned_ratio is not None else 1,
            definitions[score.criterion_id]["title"],
        )
    )
    actions = []
    for priority, score in enumerate(failures[:8], start=1):
        definition = definitions[score.criterion_id]
        actions.append(
            {
                "criterion_id": score.criterion_id,
                "title": definition["title"],
                "level": definition["level"],
                "priority": priority,
                "rationale": definition["guidance"],
                "effort": "unclassified",
                "authority": "unclassified",
                "flags": [],
                "current_percentage": round((score.owned_ratio or 0) * 100, 2),
                "failing_units": list(score.failing_units),
                "source": "fallback",
            }
        )
    return actions, False


def report_payload(
    assessment: dict[str, Any],
    rubric: dict[str, Any],
    scores: list[CriterionScore],
) -> dict[str, Any]:
    definitions = {criterion["id"]: criterion for criterion in rubric["criteria"]}
    owned_percentage = overall_percentage(scores, "owned_ratio")
    compatibility_percentage = overall_percentage(scores, "compatibility_ratio")
    status_counts, category_breakdown = score_breakdown(scores, definitions)
    category_summary = {
        category: values["percentage"] for category, values in category_breakdown.items()
    }
    actions, recommendations_ranked = next_actions(assessment, definitions, scores)
    provenance = assessment.get("provenance", {})
    extensions = owned_extension_summary(assessment)
    return {
        "schema_version": "1.0",
        "report_version": "2.0",
        "rubric_version": rubric["version"],
        "repository": assessment["repository"],
        "preferences": assessment.get("preferences", {}),
        "provenance": provenance,
        "audit_warnings": provenance_warnings(provenance),
        "summary": {
            "owned_percentage": round(owned_percentage, 4),
            "owned_level": readiness_level(owned_percentage),
            "compatibility_percentage": round(compatibility_percentage, 4),
            "compatibility_level": readiness_level(compatibility_percentage),
            "applicable_criteria": sum(score.owned_ratio is not None for score in scores),
            "skipped_criteria": sum(score.owned_ratio is None for score in scores),
            "categories": category_summary,
            "category_breakdown": category_breakdown,
            "status_counts": status_counts,
        },
        "applications": application_breakdown(assessment, rubric),
        "owned_extensions": extensions,
        "next_actions": actions,
        "recommendations_ranked": recommendations_ranked,
        "criteria": {
            score.criterion_id: {
                "title": definitions[score.criterion_id]["title"],
                "level": definitions[score.criterion_id]["level"],
                "scope": definitions[score.criterion_id]["scope"],
                "category": definitions[score.criterion_id]["category"],
                "guidance": definitions[score.criterion_id]["guidance"],
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


def parse_timestamp(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def provenance_warnings(provenance: Any) -> list[str]:
    if not isinstance(provenance, dict) or not provenance:
        return ["This report has no structured provenance; re-run the audit for a durable trail."]
    warnings: list[str] = []
    now = datetime.now(timezone.utc)
    checks = provenance.get("evidence_checks", [])
    if not isinstance(checks, list):
        return ["Evidence-check provenance is malformed."]
    for check in checks:
        if not isinstance(check, dict) or check.get("kind") != "external":
            continue
        label = check.get("label", "External evidence")
        fresh_until = parse_timestamp(check.get("fresh_until", ""))
        if fresh_until is None:
            warnings.append(f"{label}: external evidence has no verifiable freshness window.")
        elif fresh_until < now:
            warnings.append(f"{label}: external evidence is stale as of {check.get('fresh_until')}.")
    return warnings


def escape(value: Any) -> str:
    return html.escape(str(value), quote=True)


def format_ratio(value: float | None) -> str:
    return "Skipped" if value is None else f"{value * 100:.0f}%"


def status_text(status: str) -> str:
    return status.replace("_", " ").title()


def render_category_chart(summary: dict[str, Any]) -> str:
    breakdown = summary.get("category_breakdown", {})
    if not breakdown:
        breakdown = {
            key: {"percentage": value, "pass": 0, "partial": 0, "fail": 0, "skipped": 0, "total": 0}
            for key, value in summary.get("categories", {}).items()
        }
    rows: list[str] = []
    for key, values in breakdown.items():
        total = values.get("total", 0) or 1
        segments = "".join(
            f'<i class="segment {state}" style="width:{values.get(state, 0) * 100 / total:.4f}%" '
            f'title="{state.title()}: {values.get(state, 0)}"></i>'
            for state in ("pass", "partial", "fail", "skipped")
            if values.get(state, 0)
        )
        percentage = float(values.get("percentage", 0))
        rows.append(
            f'<article class="category-row" style="grid-template-columns:200px 1fr"><div class="category-heading" '
            f'style="display:grid;grid-template-columns:1fr auto;align-items:start"><div><span class="kicker">Area</span>'
            f'<h3>{escape(CATEGORY_TITLES.get(key, key))}</h3></div><strong style="font-size:18px">{percentage:.1f}%</strong></div>'
            f'<div class="category-track" role="img" aria-label="{escape(CATEGORY_TITLES.get(key, key))}: {percentage:.1f} percent met">'
            f'{segments}<b class="score-marker" style="left:{max(0, min(100, percentage)):.4f}%"></b></div>'
            f'<div class="legend-line"><span><i class="dot pass"></i>{values.get("pass", 0)} pass</span>'
            f'<span><i class="dot partial"></i>{values.get("partial", 0)} partial</span>'
            f'<span><i class="dot fail"></i>{values.get("fail", 0)} fail</span>'
            f'<span><i class="dot skipped"></i>{values.get("skipped", 0)} N/A</span></div></article>'
        )
    return "".join(rows)


def render_next_actions(payload: dict[str, Any]) -> str:
    actions = payload.get("next_actions", [])
    if not actions:
        return '<article class="empty-state">Every applicable criterion passes. Protect the baseline and watch for regressions.</article>'
    cards: list[str] = []
    flag_titles = {
        "external_account": "External account",
        "large_refactor": "Large refactor",
        "paid_service": "Paid service",
        "production_change": "Production change",
    }
    for action in actions:
        authority = action.get("authority", "unclassified")
        current_percentage = action.get("current_percentage")
        current_label = "N/A" if current_percentage is None else f"{current_percentage:.0f}%"
        badges = [
            f'<span class="badge authority-{escape(authority)}">{escape(status_text(authority))}</span>',
            f'<span class="badge">{escape(status_text(action.get("effort", "unclassified")))} effort</span>',
        ]
        badges.extend(
            f'<span class="badge flag">{escape(flag_titles.get(flag, status_text(flag)))}</span>'
            for flag in action.get("flags", [])
        )
        units = ", ".join(action.get("failing_units", [])) or "repository"
        cards.append(
            f'<article class="action-card"><div class="action-number">{action["priority"]:02d}</div>'
            f'<div><div class="badge-row">{"".join(badges)}</div><h3>{escape(action["title"])}</h3>'
            f'<p>{escape(action["rationale"])}</p><div class="action-meta"><code>{escape(action["criterion_id"])}</code>'
            f'<span>Level {action["level"]}</span><span>{current_label} currently met</span>'
            f'<span>Gap: {escape(units)}</span></div></div></article>'
        )
    notice = ""
    if not payload.get("recommendations_ranked"):
        notice = (
            '<p class="notice"><strong>Mechanical fallback:</strong> the assessment did not include an agent-ranked '
            "recommendation set, so these candidates are ordered by readiness level and score gap. "
            "Confirm repository value and authority before acting.</p>"
        )
    return notice + "".join(cards)


def render_application_section(payload: dict[str, Any]) -> str:
    applications = payload.get("applications", {})
    cards = "".join(
        f'<article class="application-card"><div><span class="kicker">Application</span><h3>{escape(app_id)}</h3>'
        f'<p>{escape(values.get("description", ""))}</p><code>{escape(values.get("path", ""))}</code></div>'
        f'<div class="application-score"><strong>{values.get("percentage", 0):.1f}%</strong>'
        f'<span>{values.get("pass", 0)} pass / {values.get("fail", 0)} fail / {values.get("not_applicable", 0)} N/A</span></div></article>'
        for app_id, values in applications.items()
    )
    app_ids = list(payload.get("repository", {}).get("applications", {}))
    header = "".join(f"<th>{escape(app_id)}</th>" for app_id in app_ids)
    rows: list[str] = []
    for criterion_id, criterion in payload["criteria"].items():
        if criterion["scope"] != "application":
            continue
        judgments = report_judgments(criterion)
        cells = "".join(
            f'<td><span class="matrix-status {escape(judgments.get(app_id, {}).get("status", "unknown"))}">'
            f'{escape(status_text(judgments.get(app_id, {}).get("status", "unknown")))}</span></td>'
            for app_id in app_ids
        )
        rows.append(
            f'<tr data-category="{escape(criterion["category"])}" data-search="{escape((criterion["title"] + " " + criterion_id).lower())}">'
            f'<td><strong>{escape(criterion["title"])}</strong><code>{escape(criterion_id)}</code></td>'
            f'<td>{escape(CATEGORY_TITLES[criterion["category"]])}</td>{cells}</tr>'
        )
    matrix = (
        '<div class="matrix-tools no-print"><label>Filter area <select id="matrix-category"><option value="">All areas</option>'
        + "".join(f'<option value="{escape(key)}">{escape(title)}</option>' for key, title in CATEGORY_TITLES.items())
        + '</select></label><label>Find criterion <input id="matrix-search" type="search" placeholder="Type a name or ID"></label></div>'
        + f'<div class="table-scroll"><table class="matrix"><thead><tr><th>Criterion</th><th>Area</th>{header}</tr></thead>'
        + f'<tbody>{"".join(rows)}</tbody></table></div>'
    )
    return f'<div class="application-grid">{cards}</div>{matrix}'


def render_failure_groups(payload: dict[str, Any]) -> str:
    grouped: dict[str, list[tuple[str, dict[str, Any]]]] = defaultdict(list)
    for criterion_id, criterion in payload["criteria"].items():
        if criterion["owned"]["ratio"] not in {None, 1.0}:
            grouped[criterion["category"]].append((criterion_id, criterion))
    if not grouped:
        return '<article class="empty-state">No applicable failures.</article>'
    sections: list[str] = []
    for category in CATEGORY_TITLES:
        failures = grouped.get(category, [])
        if not failures:
            continue
        items = "".join(
            f'<article class="failure-card"><div class="failure-score">{format_ratio(item["owned"]["ratio"])}</div>'
            f'<div><span class="kicker">Level {item["level"]}</span><h3>{escape(item["title"])}</h3>'
            f'<p>{escape(item["guidance"])}</p><div class="action-meta"><code>{escape(criterion_id)}</code>'
            f'<span>Failing: {escape(", ".join(item["failing_units"]) or "partial")}</span></div></div></article>'
            for criterion_id, item in sorted(failures, key=lambda pair: (pair[1]["level"], pair[1]["title"]))
        )
        sections.append(f'<section class="failure-group"><h3>{escape(CATEGORY_TITLES[category])}</h3>{items}</section>')
    return "".join(sections)


def render_evidence(payload: dict[str, Any]) -> str:
    sections: list[str] = []
    for identifier, item in payload["criteria"].items():
        judgments: list[str] = []
        for unit, judgment in report_judgments(item).items():
            evidence = "".join(f"<li>{escape(value)}</li>" for value in judgment.get("evidence", []))
            status = judgment.get("status", "unknown")
            judgments.append(
                f'<article class="judgment"><div class="judgment-heading"><strong>{escape(unit)}</strong>'
                f'<span class="matrix-status {escape(status)}">{escape(status_text(status))}</span>'
                f'<span class="confidence">{escape(judgment.get("confidence", "unknown"))} confidence</span></div>'
                f'<p>{escape(judgment.get("rationale", ""))}</p><ul>{evidence}</ul></article>'
            )
        sections.append(
            f'<details><summary><span><b>{escape(item["title"])}</b><small>{escape(CATEGORY_TITLES[item["category"]])} / Level {item["level"]}</small></span>'
            f'<code>{escape(identifier)}</code></summary>{"".join(judgments)}</details>'
        )
    return "".join(sections)


def render_provenance(payload: dict[str, Any]) -> str:
    provenance = payload.get("provenance", {})
    checks = provenance.get("evidence_checks", []) if isinstance(provenance, dict) else []
    check_rows = "".join(
        f'<tr><td>{escape(check.get("label", "check"))}</td><td>{escape(status_text(check.get("kind", "unknown")))}</td>'
        f'<td>{escape(check.get("checked_at", "unknown"))}</td><td>{escape(check.get("exit_status", "n/a"))}</td>'
        f'<td>{escape(check.get("summary", ""))}</td></tr>'
        for check in checks if isinstance(check, dict)
    ) or '<tr><td colspan="5">No structured evidence checks were recorded.</td></tr>'
    fields = (
        ("Rubric", payload.get("rubric_version", "unknown")),
        ("Skill", provenance.get("skill_version", "legacy") if isinstance(provenance, dict) else "legacy"),
        ("Skill fingerprint", provenance.get("skill_fingerprint", "unknown") if isinstance(provenance, dict) else "unknown"),
        ("Preferences", payload.get("preferences", {}).get("source", "skill defaults")),
        ("Repository dirty", payload.get("repository", {}).get("dirty", "unknown")),
    )
    field_cards = "".join(f'<div><span>{escape(label)}</span><strong>{escape(value)}</strong></div>' for label, value in fields)
    return (
        f'<div class="provenance-grid">{field_cards}</div><div class="table-scroll"><table><thead><tr>'
        f'<th>Evidence check</th><th>Kind</th><th>Checked</th><th>Exit</th><th>Summary</th></tr></thead><tbody>{check_rows}</tbody></table></div>'
    )


def render_progress(payload: dict[str, Any]) -> str:
    progress = payload.get("progress")
    if not isinstance(progress, dict):
        return (
            '<article class="empty-state">No previous assessment was attached. Pass <code>--previous PATH</code> '
            "on the next scoring round to show improvements, regressions, and evidence changes here.</article>"
        )
    summary = progress["summary"]
    delta = float(summary["owned_percentage_delta"])
    regression_items = "".join(
        f'<li><strong>{escape(progress["criteria"][criterion_id]["title"])}</strong>'
        f'<span>{escape(", ".join(progress["criteria"][criterion_id]["regressions"]) or "score decreased")}</span></li>'
        for criterion_id in progress.get("regressions", [])
    ) or "<li><strong>No regressions</strong><span>The new round preserved every previous pass.</span></li>"
    improvement_items = "".join(
        f'<li><strong>{escape(progress["criteria"][criterion_id]["title"])}</strong>'
        f'<span>{escape(", ".join(progress["criteria"][criterion_id]["newly_passing_units"]) or "score increased")}</span></li>'
        for criterion_id in progress.get("improvements", [])
    ) or "<li><strong>No newly passing criteria</strong><span>The score may still include evidence-only changes.</span></li>"
    direction = "positive" if delta >= 0 else "negative"
    return (
        f'<div class="progress-hero"><div class="delta {direction}"><span>Owned score delta</span>'
        f'<strong>{signed(delta)} pts</strong></div><div class="progress-stats">'
        f'<div><strong>{summary["improvement_count"]}</strong><span>improvements</span></div>'
        f'<div><strong>{summary["regression_count"]}</strong><span>regressions</span></div>'
        f'<div><strong>{summary["changed_criteria"]}</strong><span>changed criteria</span></div></div></div>'
        f'<div class="progress-columns"><article><h3>Newly earned</h3><ul>{improvement_items}</ul></article>'
        f'<article><h3>Regressions</h3><ul>{regression_items}</ul></article></div>'
    )


def render_owned_extensions(payload: dict[str, Any]) -> str:
    summary = payload.get("owned_extensions", {})
    checkpoints = summary.get("checkpoints", []) if isinstance(summary, dict) else []
    if not checkpoints:
        return '<article class="empty-state">No owned extensions were assessed in this round.</article>'
    cards: list[str] = []
    for checkpoint in checkpoints:
        controls = checkpoint.get("controls", [])
        control_rows = "".join(
            f'<li><span class="matrix-status {escape(control.get("status", "fail"))}">{escape(status_text(control.get("status", "fail")))}</span> '
            f'<strong>{escape(control.get("title", control.get("id", "control")))}</strong></li>'
            for control in controls
        )
        cards.append(
            f'<article class="judgment"><div class="judgment-heading"><strong>{escape(checkpoint.get("title", "Owned extension"))}</strong>'
            f'<span class="matrix-status {escape(checkpoint.get("status", "fail"))}">{escape(status_text(checkpoint.get("status", "fail")))}</span></div>'
            f'<p><code>{escape(checkpoint.get("id", "extension"))}</code> v{escape(checkpoint.get("version", "unknown"))}; '
            f'{len([control for control in controls if control.get("status") == "pass"])}/{len(controls)} controls passing.</p><ul>{control_rows}</ul></article>'
        )
    return (
        '<p class="notice">Owned extensions are outside the stable 82-criterion owned and compatibility scores. '
        f'Checkpoint denominator: {summary.get("checkpoint_denominator", 0)}; control denominator: {summary.get("control_denominator", 0)}.</p>'
        + "".join(cards)
    )


def render_html(payload: dict[str, Any]) -> str:
    summary = payload["summary"]
    repository = payload["repository"]
    counts = summary.get("status_counts", {})
    warnings = payload.get("audit_warnings", [])
    warning_block = ""
    if warnings:
        warning_block = '<section class="warning-block report-section"><div class="section-label">Audit warnings</div><ul>' + "".join(
            f"<li>{escape(warning)}</li>" for warning in warnings
        ) + "</ul></section>"
    generated = repository.get("generated_at", "unknown")
    commit = repository.get("commit", "unknown")
    app_count = len(repository.get("applications", {}))
    category_chart = render_category_chart(summary)
    actions = render_next_actions(payload)
    application_section = render_application_section(payload)
    failures = render_failure_groups(payload)
    evidence = render_evidence(payload)
    provenance = render_provenance(payload)
    progress = render_progress(payload)
    owned_extensions = render_owned_extensions(payload)
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light"><title>Agent Readiness - {escape(repository.get('name', 'repository'))}</title>
<style>
:root{{--ink:#171217;--muted:#6e636c;--paper:#fffdfb;--paper-deep:#f5f0f2;--panel:#faf6f8;--accent:#ed1f83;--accent-soft:#ffe4f1;--pass:#177a57;--pass-soft:#dff4eb;--partial:#b06b08;--partial-soft:#fff0cc;--fail:#bd3151;--fail-soft:#ffe3e9;--skip:#8a8088;--skip-soft:#ece7eb;--line:#ded4dc;--shadow:0 18px 50px rgba(70,31,55,.09)}}*{{box-sizing:border-box}}html{{scroll-behavior:smooth}}body{{margin:0;background:linear-gradient(145deg,#fffdfb 0%,#fbf6f8 55%,#f4edf1 100%);color:var(--ink);font:15px/1.55 "Avenir Next","Segoe UI",sans-serif}}body:before{{content:"";position:fixed;inset:0;pointer-events:none;opacity:.22;background-image:url("data:image/svg+xml,%3Csvg viewBox='0 0 180 180' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='.9' numOctaves='3' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)' opacity='.07'/%3E%3C/svg%3E")}}main{{position:relative;max-width:1180px;margin:auto;padding:30px 34px 90px}}.toolbar{{display:flex;justify-content:space-between;align-items:center;margin-bottom:42px}}.brand{{font-size:12px;font-weight:850;letter-spacing:.18em;text-transform:uppercase}}.brand i{{display:inline-block;width:9px;height:9px;background:var(--accent);border-radius:50%;margin-right:9px}}button,select,input{{font:inherit}}button{{border:1px solid var(--line);background:rgba(255,255,255,.84);padding:9px 13px;border-radius:999px;color:var(--ink);cursor:pointer}}button.primary{{background:var(--ink);border-color:var(--ink);color:white}}.hero{{display:grid;grid-template-columns:minmax(0,1.4fr) minmax(260px,.6fr);gap:44px;align-items:end;padding:50px 0 38px;border-top:1px solid var(--ink);border-bottom:5px solid var(--ink)}}.eyebrow,.section-label,.kicker{{color:var(--accent);font-size:11px;font-weight:850;letter-spacing:.17em;text-transform:uppercase}}h1,h2,h3,p{{margin-top:0}}h1{{max-width:800px;margin:12px 0 20px;font:700 clamp(54px,8vw,96px)/.88 "Iowan Old Style","Palatino Linotype",serif;letter-spacing:-.065em}}h2{{margin-bottom:12px;font:700 clamp(34px,5vw,58px)/.98 "Iowan Old Style","Palatino Linotype",serif;letter-spacing:-.045em}}h3{{margin-bottom:7px;font-size:18px;line-height:1.2}}.hero-copy{{max-width:660px;color:var(--muted);font-size:17px}}.score-lockup{{text-align:right}}.score-lockup strong{{display:block;font-size:clamp(76px,10vw,128px);font-weight:900;line-height:.75;letter-spacing:-.09em}}.score-lockup span{{display:block;margin-top:24px;font-size:13px;font-weight:850;letter-spacing:.16em;text-transform:uppercase}}.score-lockup em{{display:inline-block;margin-top:8px;padding:5px 10px;background:var(--accent);color:white;font-style:normal;font-weight:850}}.meta-ribbon{{display:grid;grid-template-columns:repeat(5,1fr);border-bottom:1px solid var(--line)}}.meta-ribbon div{{padding:17px 16px;border-right:1px solid var(--line)}}.meta-ribbon div:last-child{{border-right:0}}.meta-ribbon span,.provenance-grid span{{display:block;color:var(--muted);font-size:10px;font-weight:800;letter-spacing:.12em;text-transform:uppercase}}.meta-ribbon strong{{display:block;margin-top:3px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}.report-section{{padding:70px 0;border-bottom:1px solid var(--line)}}.section-intro{{display:grid;grid-template-columns:minmax(0,.7fr) minmax(300px,.3fr);gap:40px;align-items:end;margin-bottom:34px}}.section-intro p{{color:var(--muted);margin-bottom:5px}}.stats{{display:grid;grid-template-columns:repeat(4,1fr);gap:10px}}.stat{{padding:16px;background:rgba(255,255,255,.66);border:1px solid var(--line)}}.stat strong{{display:block;font-size:27px}}.stat span{{color:var(--muted);font-size:11px;text-transform:uppercase;letter-spacing:.1em}}.category-row{{display:grid;grid-template-columns:220px 1fr;gap:22px;padding:20px 0;border-top:1px solid var(--line)}}.category-heading{{display:flex;justify-content:space-between;gap:14px}}.category-heading h3{{margin:3px 0}}.category-heading strong{{font-size:24px}}.category-track{{position:relative;display:flex;height:22px;margin-top:7px;overflow:hidden;background:var(--skip-soft);border-radius:3px}}.segment{{height:100%}}.segment.pass,.dot.pass{{background:var(--pass)}}.segment.partial,.dot.partial{{background:var(--partial)}}.segment.fail,.dot.fail{{background:var(--fail)}}.segment.skipped,.dot.skipped{{background:var(--skip)}}.score-marker{{position:absolute;top:-5px;bottom:-5px;width:3px;background:var(--ink);transform:translateX(-1px)}}.legend-line{{display:flex;gap:15px;margin-top:8px;color:var(--muted);font-size:11px}}.dot{{display:inline-block;width:7px;height:7px;border-radius:50%;margin-right:5px}}.notice,.warning-block{{padding:17px 20px;background:#fff8dd;border-left:5px solid #e1a321;color:#6d571a}}.action-card{{display:grid;grid-template-columns:70px 1fr;gap:22px;padding:25px 0;border-top:1px solid var(--line);break-inside:avoid}}.action-number{{color:var(--accent);font:700 43px/1 "Iowan Old Style",serif}}.badge-row,.action-meta{{display:flex;flex-wrap:wrap;gap:7px;align-items:center}}.badge{{display:inline-block;padding:4px 8px;border:1px solid var(--line);border-radius:999px;color:var(--muted);font-size:10px;font-weight:800;letter-spacing:.06em;text-transform:uppercase}}.authority-autonomous{{background:var(--pass-soft);border-color:#afd8c6;color:#0b6848}}.authority-approval_required{{background:var(--partial-soft);border-color:#e8c979;color:#805100}}.authority-deferred,.flag{{background:var(--fail-soft);border-color:#e7aabb;color:#8e193b}}.action-card h3{{margin:10px 0 5px;font:700 25px/1.1 "Iowan Old Style",serif}}.action-card p,.failure-card p,.application-card p{{color:var(--muted)}}code{{display:inline-block;max-width:100%;overflow-wrap:anywhere;background:#eee6eb;padding:2px 6px;border-radius:3px;font:12px/1.4 ui-monospace,SFMono-Regular,Menlo,monospace}}.action-meta{{color:var(--muted);font-size:11px}}.application-grid{{display:grid;grid-template-columns:repeat(2,1fr);gap:12px;margin-bottom:30px}}.application-card{{display:flex;justify-content:space-between;gap:24px;padding:22px;background:rgba(255,255,255,.7);border:1px solid var(--line);box-shadow:var(--shadow);break-inside:avoid}}.application-card p{{margin-bottom:8px}}.application-score{{min-width:130px;text-align:right}}.application-score strong{{display:block;font-size:36px}}.application-score span{{color:var(--muted);font-size:11px}}.matrix-tools{{display:flex;gap:12px;margin:18px 0}}.matrix-tools label{{color:var(--muted);font-size:11px;font-weight:800;text-transform:uppercase;letter-spacing:.08em}}select,input{{display:block;min-width:220px;margin-top:5px;padding:9px;border:1px solid var(--line);background:white;color:var(--ink)}}.table-scroll{{overflow-x:auto;border:1px solid var(--line);background:rgba(255,255,255,.65)}}table{{width:100%;border-collapse:collapse;font-size:12px}}th,td{{padding:11px 12px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}}th{{background:#f1eaee;font-size:10px;letter-spacing:.08em;text-transform:uppercase}}tbody tr:last-child td{{border-bottom:0}}.matrix td:first-child strong,.matrix td:first-child code{{display:block}}.matrix-status{{display:inline-block;padding:3px 7px;border-radius:999px;font-size:9px;font-weight:850;letter-spacing:.05em;text-transform:uppercase;white-space:nowrap}}.matrix-status.pass{{background:var(--pass-soft);color:#0b6848}}.matrix-status.fail{{background:var(--fail-soft);color:#941a3c}}.matrix-status.not_applicable{{background:var(--skip-soft);color:#645a62}}.failure-group{{margin-top:34px}}.failure-group>h3{{padding-bottom:9px;border-bottom:3px solid var(--ink);font-size:13px;letter-spacing:.12em;text-transform:uppercase}}.failure-card{{display:grid;grid-template-columns:85px 1fr;gap:20px;padding:19px 0;border-bottom:1px solid var(--line);break-inside:avoid}}.failure-score{{font-size:28px;font-weight:900}}details{{border-top:1px solid var(--line)}}summary{{display:flex;justify-content:space-between;gap:16px;padding:15px 0;cursor:pointer;list-style:none}}summary::-webkit-details-marker{{display:none}}summary small{{display:block;color:var(--muted);font-weight:400}}.judgment{{margin:0 0 14px;padding:18px;background:rgba(255,255,255,.7);border-left:3px solid var(--line);break-inside:avoid}}.judgment-heading{{display:flex;flex-wrap:wrap;gap:8px;align-items:center}}.confidence{{color:var(--muted);font-size:11px}}.judgment p{{margin:10px 0 5px}}.judgment ul{{margin:4px 0;padding-left:20px;color:var(--muted)}}.provenance-grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin-bottom:20px}}.provenance-grid div{{padding:15px;border:1px solid var(--line);background:rgba(255,255,255,.6)}}.provenance-grid strong{{display:block;margin-top:4px;overflow-wrap:anywhere;font-size:12px}}.empty-state{{padding:30px;border:1px dashed var(--line);color:var(--muted);text-align:center}}.document-footer{{display:flex;justify-content:space-between;gap:20px;padding:30px 0;color:var(--muted);font-size:11px}}.print-footer{{display:none}}@media(max-width:820px){{main{{padding:22px 20px 70px}}.hero,.section-intro{{grid-template-columns:1fr}}.score-lockup{{text-align:left}}.meta-ribbon{{grid-template-columns:1fr 1fr}}.meta-ribbon div{{border-bottom:1px solid var(--line)}}.stats{{grid-template-columns:1fr 1fr}}.category-row{{grid-template-columns:1fr}}.application-grid{{grid-template-columns:1fr}}.provenance-grid{{grid-template-columns:1fr 1fr}}.toolbar-actions button:not(.primary){{display:none}}}}@media print{{@page{{size:Letter;margin:.55in}}body{{background:white;font-size:10px;-webkit-print-color-adjust:exact;print-color-adjust:exact}}body:before,.no-print{{display:none!important}}main{{max-width:none;padding:0}}.toolbar{{display:none}}.hero{{min-height:8.3in;align-content:center;break-after:page}}h1{{font-size:66px}}h2{{font-size:35px}}.score-lockup strong{{font-size:94px}}.report-section{{padding:30px 0}}.report-section.major{{break-before:page}}.section-intro{{margin-bottom:20px}}.category-row{{grid-template-columns:165px 1fr;padding:12px 0}}.category-track{{height:15px}}.action-card{{padding:14px 0}}.application-card{{box-shadow:none}}.table-scroll{{overflow:visible}}table{{font-size:8px}}th,td{{padding:6px}}details{{break-inside:auto}}details>*:not(summary){{display:block!important}}summary{{padding:9px 0}}.judgment{{padding:10px;margin-bottom:8px}}.document-footer{{display:none}}.print-footer{{display:block;position:fixed;left:0;right:0;bottom:-.34in;padding-top:5px;border-top:1px solid var(--line);color:var(--muted);font-size:8px}}}}
</style><style>
.progress-hero{{display:grid;grid-template-columns:.8fr 1.2fr;gap:14px}}.delta,.progress-stats>div{{padding:22px;border:1px solid var(--line);background:rgba(255,255,255,.7)}}.delta span,.progress-stats span{{display:block;color:var(--muted);font-size:10px;font-weight:800;letter-spacing:.1em;text-transform:uppercase}}.delta strong{{display:block;margin-top:8px;font-size:44px}}.delta.positive strong{{color:var(--pass)}}.delta.negative strong{{color:var(--fail)}}.progress-stats{{display:grid;grid-template-columns:repeat(3,1fr);gap:10px}}.progress-stats strong{{display:block;font-size:30px}}.progress-columns{{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-top:14px}}.progress-columns article{{padding:20px;border:1px solid var(--line);background:rgba(255,255,255,.55)}}.progress-columns ul{{list-style:none;margin:0;padding:0}}.progress-columns li{{display:flex;justify-content:space-between;gap:16px;padding:9px 0;border-top:1px solid var(--line)}}.progress-columns li span{{color:var(--muted);font-size:11px;text-align:right}}@media(max-width:820px){{.progress-columns,.progress-hero{{grid-template-columns:1fr}}}}
</style><style>
@media print{{.hero{{min-height:7.2in;break-after:avoid}}.meta-ribbon{{break-after:page}}.category-row{{grid-template-columns:200px 1fr}}.category-heading{{display:grid;grid-template-columns:1fr auto;align-items:start}}.category-heading strong{{font-size:18px}}.print-footer{{display:none!important}}}}
</style></head><body><main><div class="toolbar no-print"><div class="brand"><i></i>Agent Readiness / Evidence Dossier</div><div class="toolbar-actions"><button id="collapse-all">Collapse evidence</button><button id="expand-all">Expand evidence</button><button class="primary" onclick="window.print()">Print / Save PDF</button></div></div>
<header class="hero" style="min-height:7.2in;break-after:avoid"><div><div class="eyebrow">Repository capability audit</div><h1>{escape(repository.get('name', 'Repository'))}</h1><p class="hero-copy">An evidence-backed measure of how safely and effectively coding agents can understand, change, verify, and operate this repository.</p></div><div class="score-lockup"><strong>{summary['owned_percentage']:.1f}%</strong><span>Owned readiness</span><em>Level {summary['owned_level']}</em></div></header>
<section class="meta-ribbon" style="break-after:page"><div><span>Compatibility</span><strong>{summary['compatibility_percentage']:.1f}% / Level {summary['compatibility_level']}</strong></div><div><span>Applications</span><strong>{app_count}</strong></div><div><span>Commit</span><strong>{escape(commit[:12])}</strong></div><div><span>Audit state</span><strong>{'Dirty working tree' if repository.get('dirty') else 'Clean commit'}</strong></div><div><span>Generated</span><strong>{escape(generated)}</strong></div></section>
{warning_block}<section id="health" class="report-section major"><div class="section-intro"><div><div class="section-label">01 / Category health</div><h2>Where agents can move confidently.</h2></div><div class="stats"><div class="stat"><strong>{counts.get('pass', 0)}</strong><span>Passing</span></div><div class="stat"><strong>{counts.get('partial', 0)}</strong><span>Partial</span></div><div class="stat"><strong>{counts.get('fail', 0)}</strong><span>Failing</span></div><div class="stat"><strong>{counts.get('skipped', 0)}</strong><span>Not applicable</span></div></div></div>{category_chart}</section>
<section id="owned-extensions" class="report-section"><div class="section-intro"><div><div class="section-label">01a / Owned extensions</div><h2>Additional standards, separately counted.</h2></div><p>Extensions provide repository-owned checkpoints without altering the stable 82-criterion compatibility score.</p></div>{owned_extensions}</section>
<section id="progress" class="report-section major"><div class="section-intro"><div><div class="section-label">02 / Progress</div><h2>What changed since the last round.</h2></div><p>Score movement is secondary to durable capability gains. Regressions remain prominent even when the total score rises.</p></div>{progress}</section>
<section id="actions" class="report-section major"><div class="section-intro"><div><div class="section-label">03 / Priority queue</div><h2>The next capabilities worth earning.</h2></div><p>Ranked work should reflect repository value, dependency order, implementation effort, and the authority required to proceed - never score points alone.</p></div>{actions}</section>
<section id="applications" class="report-section major"><div class="section-intro"><div><div class="section-label">04 / Application surface</div><h2>Readiness is rarely uniform.</h2></div><p>Application-scoped criteria expose where one deployable surface is safe for agents while another still carries operational risk.</p></div>{application_section}</section>
<section id="failures" class="report-section major"><div class="section-intro"><div><div class="section-label">05 / Capability gaps</div><h2>Every failure, without camouflage.</h2></div><p>Partial results remain visible. Inapplicable applications are excluded from the owned denominator but preserved for auditability.</p></div>{failures}</section>
<section id="evidence" class="report-section major"><div class="section-intro"><div><div class="section-label">06 / Evidence ledger</div><h2>Why each judgment exists.</h2></div><p>Paths, commands, rationale, confidence, and applicability are the durable record. Expand any criterion to inspect its evidence.</p></div>{evidence}</section>
<section id="provenance" class="report-section major"><div class="section-intro"><div><div class="section-label">07 / Provenance</div><h2>Trust the report's lineage.</h2></div><p>Version, preferences, repository state, and freshness checks make this assessment reproducible and honest about external evidence.</p></div>{provenance}</section>
<footer class="document-footer"><span>agent-readiness-scoring {escape(payload.get('provenance', {}).get('skill_version', 'legacy'))}</span><span>Owned score excludes genuinely inapplicable application surfaces.</span></footer></main>
<script>const details=[...document.querySelectorAll('details')];document.getElementById('collapse-all').addEventListener('click',()=>details.forEach(item=>item.open=false));document.getElementById('expand-all').addEventListener('click',()=>details.forEach(item=>item.open=true));const category=document.getElementById('matrix-category');const search=document.getElementById('matrix-search');function filterMatrix(){{const term=(search.value||'').toLowerCase();document.querySelectorAll('.matrix tbody tr').forEach(row=>{{row.hidden=Boolean(category.value&&row.dataset.category!==category.value)||Boolean(term&&!row.dataset.search.includes(term));}})}}category.addEventListener('change',filterMatrix);search.addEventListener('input',filterMatrix);window.addEventListener('beforeprint',()=>details.forEach(item=>item.open=true));</script></body></html>"""


def find_chromium(explicit: Path | None = None) -> Path | None:
    if explicit is not None:
        candidate = explicit.expanduser().resolve()
        if not candidate.is_file():
            raise AssessmentError(f"PDF browser executable not found: {candidate}")
        return candidate
    for name in ("chromium", "chromium-browser", "google-chrome", "google-chrome-stable", "chrome"):
        found = shutil.which(name)
        if found:
            return Path(found).resolve()
    for candidate in (
        Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
        Path("/Applications/Chromium.app/Contents/MacOS/Chromium"),
        Path("/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"),
    ):
        if candidate.is_file():
            return candidate
    return None


def render_pdf(html_path: Path, pdf_path: Path, browser: Path | None = None) -> Path:
    executable = find_chromium(browser)
    if executable is None:
        raise AssessmentError(
            "PDF output requires an installed Chrome or Chromium browser. Install one or pass --browser PATH; "
            "the skill will not download a browser automatically."
        )
    source = html_path.resolve()
    destination = pdf_path.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        destination.unlink()
    try:
        with tempfile.TemporaryDirectory(prefix="agent-readiness-pdf-") as profile:
            result = subprocess.run(
                [
                    str(executable),
                    "--headless=new",
                    "--disable-gpu",
                    "--no-pdf-header-footer",
                    "--allow-file-access-from-files",
                    "--run-all-compositor-stages-before-draw",
                    "--virtual-time-budget=1500",
                    f"--user-data-dir={profile}",
                    f"--print-to-pdf={destination}",
                    source.as_uri(),
                ],
                check=False,
                capture_output=True,
                text=True,
                timeout=60,
            )
    except subprocess.TimeoutExpired as error:
        # Chrome occasionally finishes printing a large document and then lingers while shutting
        # down its isolated profile. The PDF is the contract, so accept a completed artifact even
        # when the browser process misses the cleanup deadline.
        if destination.is_file() and destination.stat().st_size >= 1000:
            return destination
        raise AssessmentError("Chromium PDF rendering timed out after 60 seconds without a usable PDF.") from error
    if result.returncode != 0 or not destination.is_file() or destination.stat().st_size < 1000:
        detail = (result.stderr or result.stdout).strip()[-1000:]
        raise AssessmentError(f"Chromium PDF rendering failed ({result.returncode}): {detail}")
    return destination


def normalize_report(value: dict[str, Any], rubric: dict[str, Any]) -> dict[str, Any]:
    if isinstance(value.get("summary"), dict) and isinstance(value.get("criteria"), dict):
        return value
    scores = score_assessment(value, rubric)
    return report_payload(value, rubric, scores)


def report_judgments(criterion: dict[str, Any]) -> dict[str, dict[str, Any]]:
    assessment = criterion.get("assessment", {})
    if criterion.get("scope") == "repository":
        return {"repository": assessment} if isinstance(assessment, dict) else {}
    applications = assessment.get("applications", {}) if isinstance(assessment, dict) else {}
    return applications if isinstance(applications, dict) else {}


def comparison_payload(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    before_criteria = before.get("criteria", {})
    after_criteria = after.get("criteria", {})
    if set(before_criteria) != set(after_criteria):
        raise AssessmentError("Comparison inputs must contain the same criterion IDs.")
    changes: dict[str, Any] = {}
    regression_ids: list[str] = []
    improvement_ids: list[str] = []
    for criterion_id in before_criteria:
        old = before_criteria[criterion_id]
        new = after_criteria[criterion_id]
        old_judgments = report_judgments(old)
        new_judgments = report_judgments(new)
        units = sorted(set(old_judgments) | set(new_judgments))
        newly_passing: list[str] = []
        regressions: list[str] = []
        applicability_changes: list[dict[str, str | None]] = []
        evidence_changes: list[str] = []
        confidence_changes: list[dict[str, str | None]] = []
        for unit in units:
            old_judgment = old_judgments.get(unit, {})
            new_judgment = new_judgments.get(unit, {})
            old_status = old_judgment.get("status")
            new_status = new_judgment.get("status")
            if old_status != "pass" and new_status == "pass":
                newly_passing.append(unit)
            if old_status == "pass" and new_status != "pass":
                regressions.append(unit)
            if (old_status == "not_applicable") != (new_status == "not_applicable"):
                applicability_changes.append({"unit": unit, "before": old_status, "after": new_status})
            if old_judgment.get("evidence") != new_judgment.get("evidence"):
                evidence_changes.append(unit)
            if old_judgment.get("confidence") != new_judgment.get("confidence"):
                confidence_changes.append(
                    {"unit": unit, "before": old_judgment.get("confidence"), "after": new_judgment.get("confidence")}
                )
        old_ratio = old.get("owned", {}).get("ratio")
        new_ratio = new.get("owned", {}).get("ratio")
        ratio_delta = None if old_ratio is None or new_ratio is None else round(new_ratio - old_ratio, 6)
        changed = bool(
            newly_passing
            or regressions
            or applicability_changes
            or evidence_changes
            or confidence_changes
            or old_ratio != new_ratio
        )
        if regressions or (ratio_delta is not None and ratio_delta < 0):
            regression_ids.append(criterion_id)
        if newly_passing or (ratio_delta is not None and ratio_delta > 0):
            improvement_ids.append(criterion_id)
        changes[criterion_id] = {
            "title": new.get("title", old.get("title", criterion_id)),
            "level": new.get("level", old.get("level")),
            "before_owned_ratio": old_ratio,
            "after_owned_ratio": new_ratio,
            "owned_ratio_delta": ratio_delta,
            "newly_passing_units": newly_passing,
            "regressions": regressions,
            "applicability_changes": applicability_changes,
            "evidence_changes": evidence_changes,
            "confidence_changes": confidence_changes,
            "changed": changed,
        }
    before_summary = before["summary"]
    after_summary = after["summary"]
    return {
        "schema_version": "1.0",
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "before": {
            "repository": before.get("repository", {}),
            "owned_percentage": before_summary["owned_percentage"],
            "owned_level": before_summary["owned_level"],
            "compatibility_percentage": before_summary["compatibility_percentage"],
            "compatibility_level": before_summary["compatibility_level"],
        },
        "after": {
            "repository": after.get("repository", {}),
            "owned_percentage": after_summary["owned_percentage"],
            "owned_level": after_summary["owned_level"],
            "compatibility_percentage": after_summary["compatibility_percentage"],
            "compatibility_level": after_summary["compatibility_level"],
        },
        "summary": {
            "owned_percentage_delta": round(after_summary["owned_percentage"] - before_summary["owned_percentage"], 4),
            "compatibility_percentage_delta": round(
                after_summary["compatibility_percentage"] - before_summary["compatibility_percentage"], 4
            ),
            "owned_level_delta": after_summary["owned_level"] - before_summary["owned_level"],
            "regression_count": len(regression_ids),
            "improvement_count": len(improvement_ids),
            "changed_criteria": sum(change["changed"] for change in changes.values()),
        },
        "regressions": regression_ids,
        "improvements": improvement_ids,
        "criteria": changes,
    }


def signed(value: float) -> str:
    return f"{value:+.2f}"


def format_ratio_delta(value: float | None) -> str:
    return "—" if value is None else f"{value * 100:+.0f} pp"


def render_comparison_markdown(comparison: dict[str, Any]) -> str:
    before = comparison["before"]
    after = comparison["after"]
    summary = comparison["summary"]
    lines = [
        "# Agent Readiness Comparison",
        "",
        f"Generated: {comparison['generated_at']}",
        "",
        "## Score Delta",
        "",
        f"- **Owned:** {before['owned_percentage']:.2f}% → {after['owned_percentage']:.2f}% ({signed(summary['owned_percentage_delta'])} points)",
        f"- **Compatibility:** {before['compatibility_percentage']:.2f}% → {after['compatibility_percentage']:.2f}% ({signed(summary['compatibility_percentage_delta'])} points)",
        f"- Regressions: **{summary['regression_count']}**; improvements: **{summary['improvement_count']}**; changed criteria: {summary['changed_criteria']}",
        "",
        "## Regressions",
        "",
    ]
    regressions = comparison["regressions"]
    if not regressions:
        lines.append("- None.")
    for criterion_id in regressions:
        change = comparison["criteria"][criterion_id]
        lines.append(f"- **{change['title']}** (`{criterion_id}`) — units: {', '.join(change['regressions']) or 'score decreased'}")
    lines.extend(["", "## Improvements", ""])
    improvements = comparison["improvements"]
    if not improvements:
        lines.append("- None.")
    for criterion_id in improvements:
        change = comparison["criteria"][criterion_id]
        lines.append(f"- **{change['title']}** (`{criterion_id}`) — units: {', '.join(change['newly_passing_units']) or 'score increased'}")
    lines.extend(["", "## All Changes", "", "| Criterion | Before | After | Delta | Evidence | Confidence |", "|---|---:|---:|---:|---:|---:|"])
    for criterion_id, change in comparison["criteria"].items():
        if not change["changed"]:
            continue
        old = format_ratio(change["before_owned_ratio"])
        new = format_ratio(change["after_owned_ratio"])
        delta = "—" if change["owned_ratio_delta"] is None else f"{change['owned_ratio_delta'] * 100:+.0f} pp"
        lines.append(
            f"| {change['title']} (`{criterion_id}`) | {old} | {new} | {delta} | "
            f"{len(change['evidence_changes'])} | {len(change['confidence_changes'])} |"
        )
    return "\n".join(lines).rstrip() + "\n"


def render_comparison_html(comparison: dict[str, Any]) -> str:
    summary = comparison["summary"]
    rows = "".join(
        f'<tr class="{"regression" if criterion_id in comparison["regressions"] else "improvement" if criterion_id in comparison["improvements"] else ""}">'
        f'<td><strong>{escape(change["title"])}</strong><br><code>{escape(criterion_id)}</code></td>'
        f'<td>{format_ratio(change["before_owned_ratio"])}</td><td>{format_ratio(change["after_owned_ratio"])}</td>'
        f'<td>{format_ratio_delta(change["owned_ratio_delta"])}</td>'
        f'<td>{escape(", ".join(change["regressions"]) or "—")}</td></tr>'
        for criterion_id, change in comparison["criteria"].items() if change["changed"]
    ) or '<tr><td colspan="5">No assessment changes.</td></tr>'
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Agent Readiness Comparison</title><style>
body{{margin:0;background:#fffdfb;color:#17141a;font:15px/1.5 ui-sans-serif,system-ui,sans-serif}}main{{max-width:1000px;margin:auto;padding:54px 28px}}h1{{font-size:54px;letter-spacing:-.05em;margin:0 0 12px}}.delta{{font-size:70px;font-weight:900;color:{'#14855f' if summary['owned_percentage_delta'] >= 0 else '#ba1b45'}}}.cards{{display:flex;gap:12px;margin:28px 0}}.card{{flex:1;background:#f7f2f6;border-radius:12px;padding:18px}}table{{width:100%;border-collapse:collapse}}th,td{{text-align:left;padding:12px;border-bottom:1px solid #e5dce4}}tr.regression{{background:#fff0f3}}tr.improvement{{background:#effaf5}}code{{background:#efe7ed;padding:2px 6px;border-radius:4px}}@media(max-width:650px){{.cards{{display:block}}.card{{margin-bottom:10px}}}}
</style></head><body><main><div>AGENT READINESS COMPARISON</div><h1>What changed?</h1><div class="delta">{signed(summary['owned_percentage_delta'])} pts</div><section class="cards"><div class="card"><strong>Before</strong><br>{comparison['before']['owned_percentage']:.2f}% · Level {comparison['before']['owned_level']}</div><div class="card"><strong>After</strong><br>{comparison['after']['owned_percentage']:.2f}% · Level {comparison['after']['owned_level']}</div><div class="card"><strong>Regressions</strong><br>{summary['regression_count']}</div></section><table><thead><tr><th>Criterion</th><th>Before</th><th>After</th><th>Delta</th><th>Regressed units</th></tr></thead><tbody>{rows}</tbody></table></main></body></html>"""


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


def doctor_report(repo: Path | None, rubric: dict[str, Any]) -> dict[str, Any]:
    checks: list[dict[str, str]] = []

    def add(name: str, status: str, detail: str) -> None:
        checks.append({"name": name, "status": status, "detail": detail})

    try:
        version = package_version()
        fingerprint = package_fingerprint()
        add("package", "pass", f"version {version}, fingerprint {fingerprint[:12]}")
    except AssessmentError as error:
        version = "unknown"
        fingerprint = "unknown"
        add("package", "fail", str(error))
    add("rubric", "pass", f"{len(rubric['criteria'])} criteria, version {rubric['version']}")
    add("python", "pass", f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}")
    git_path = shutil.which("git")
    add("git", "pass" if git_path else "fail", git_path or "git is not installed")
    pdf_browser = find_chromium()
    add(
        "pdf browser",
        "pass" if pdf_browser else "info",
        str(pdf_browser) if pdf_browser else "Chrome/Chromium not found; HTML reports remain available",
    )
    metadata_path = SKILL_ROOT / VENDOR_METADATA
    if metadata_path.exists():
        try:
            metadata = read_json(metadata_path)
            expected = metadata.get("fingerprint")
            status = "pass" if expected == fingerprint else "fail"
            add("vendor metadata", status, f"recorded {str(expected)[:12]}, current {fingerprint[:12]}")
        except AssessmentError as error:
            add("vendor metadata", "fail", str(error))
    else:
        add("vendor metadata", "info", "canonical/source installation; no vendor metadata")
    if repo is not None:
        resolved = repo.resolve()
        commit = safe_git(resolved, "rev-parse", "HEAD")
        add("repository", "pass" if commit else "fail", commit or f"not a Git repository: {resolved}")
        preference_path = resolved / "AGENT_READINESS_PREFERENCES.md"
        if preference_path.is_file():
            add("preferences", "pass", f"root preferences: {preference_path}")
        else:
            add("preferences", "info", "root preferences absent; bundled defaults will apply")
    return {
        "version": version,
        "fingerprint": fingerprint,
        "ok": all(check["status"] != "fail" for check in checks),
        "checks": checks,
    }


def vendor_plan(target: Path) -> dict[str, Any]:
    target = target.resolve()
    if target == SKILL_ROOT.resolve():
        raise AssessmentError("Refusing to vendor the package over its canonical source directory.")
    source_checksums = package_file_checksums()
    files: list[dict[str, str]] = []
    for relative, checksum in source_checksums.items():
        destination = target / relative
        if not destination.exists():
            status = "missing"
        elif not destination.is_file():
            status = "conflict"
        elif sha256_file(destination) == checksum:
            status = "current"
        else:
            status = "drifted"
        files.append({"path": relative, "status": status, "checksum": checksum})
    existing_metadata: dict[str, Any] = {}
    metadata_path = target / VENDOR_METADATA
    if metadata_path.is_file():
        try:
            existing_metadata = read_json(metadata_path)
        except AssessmentError:
            existing_metadata = {}
    previously_managed = existing_metadata.get("files", {})
    unmanaged = sorted(
        relative for relative in previously_managed
        if relative not in source_checksums and (target / relative).exists()
    ) if isinstance(previously_managed, dict) else []
    return {
        "target": str(target),
        "version": package_version(),
        "fingerprint": package_fingerprint(),
        "files": files,
        "previously_managed_but_retained": unmanaged,
        "changed": any(item["status"] != "current" for item in files),
    }


def apply_vendor_plan(plan: dict[str, Any]) -> None:
    conflicts = [item["path"] for item in plan["files"] if item["status"] == "conflict"]
    if conflicts:
        raise AssessmentError(f"Vendoring conflicts with non-files: {', '.join(conflicts)}")
    target = Path(plan["target"])
    for item in plan["files"]:
        if item["status"] == "current":
            continue
        source = SKILL_ROOT / item["path"]
        destination = target / item["path"]
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        shutil.copymode(source, destination)
    metadata = {
        "schema_version": "1.0",
        "package": "agent-readiness-scoring",
        "version": plan["version"],
        "fingerprint": plan["fingerprint"],
        "files": package_file_checksums(),
    }
    target.mkdir(parents=True, exist_ok=True)
    write_json(target / VENDOR_METADATA, metadata)
    verification = vendor_plan(target)
    remaining = [item["path"] for item in verification["files"] if item["status"] != "current"]
    if remaining:
        raise AssessmentError(f"Vendored package failed verification: {', '.join(remaining)}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rubric", type=Path, default=DEFAULT_RUBRIC)
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init", help="Create an unscored assessment skeleton.")
    init_parser.add_argument("--repo", type=Path, required=True)
    init_parser.add_argument(
        "--app",
        action="append",
        type=parse_application,
        default=[],
        metavar="ID=PATH",
        help="Discovered application identifier and repository-relative path; repeat for each app.",
    )
    init_parser.add_argument("--output", type=Path, required=True)

    validate_parser = subparsers.add_parser("validate", help="Validate a completed assessment.")
    validate_parser.add_argument("--assessment", type=Path, required=True)

    score_parser = subparsers.add_parser("score", help="Validate, score, and render a report.")
    score_parser.add_argument("--assessment", type=Path, required=True)
    score_parser.add_argument("--output-dir", type=Path, required=True)
    score_parser.add_argument("--previous", type=Path, help="Previous assessment or report JSON for an embedded progress section.")
    score_parser.add_argument("--pdf", action="store_true", help="Also print the HTML report to PDF with Chrome/Chromium.")
    score_parser.add_argument("--browser", type=Path, help="Explicit Chrome/Chromium executable for --pdf.")

    compare_parser = subparsers.add_parser("compare", help="Compare two assessments or report JSON files.")
    compare_parser.add_argument("--before", type=Path, required=True)
    compare_parser.add_argument("--after", type=Path, required=True)
    compare_parser.add_argument("--output-dir", type=Path, required=True)
    compare_parser.add_argument("--pdf", action="store_true", help="Also print the HTML comparison to PDF.")
    compare_parser.add_argument("--browser", type=Path, help="Explicit Chrome/Chromium executable for --pdf.")

    doctor_parser = subparsers.add_parser("doctor", help="Check package health and repository readiness inputs.")
    doctor_parser.add_argument("--repo", type=Path)
    doctor_parser.add_argument("--json", action="store_true", dest="as_json")

    vendor_parser = subparsers.add_parser("vendor", help="Plan or apply a safe vendored-package sync.")
    vendor_parser.add_argument("--target", type=Path, required=True)
    vendor_parser.add_argument("--apply", action="store_true")
    vendor_parser.add_argument("--json", action="store_true", dest="as_json")

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
            html_path = output_dir / "agent-readiness-report.html"
            payload = report_payload(assessment, rubric, scores)
            progress = None
            if arguments.previous:
                previous = normalize_report(read_json(arguments.previous), rubric)
                progress = comparison_payload(previous, payload)
                payload["progress"] = progress
            markdown_path.write_text(render_markdown(assessment, rubric, scores, progress), encoding="utf-8")
            write_json(json_path, payload)
            html_path.write_text(render_html(payload), encoding="utf-8")
            pdf_path = output_dir / "agent-readiness-report.pdf"
            if arguments.pdf:
                render_pdf(html_path, pdf_path, arguments.browser)
            print(
                f"Owned: Level {payload['summary']['owned_level']} "
                f"({payload['summary']['owned_percentage']:.2f}%). "
                f"Compatibility: Level {payload['summary']['compatibility_level']} "
                f"({payload['summary']['compatibility_percentage']:.2f}%)."
            )
            print(f"Markdown: {markdown_path}")
            print(f"JSON: {json_path}")
            print(f"HTML: {html_path}")
            if arguments.pdf:
                print(f"PDF: {pdf_path}")
        elif arguments.command == "compare":
            before = normalize_report(read_json(arguments.before), rubric)
            after = normalize_report(read_json(arguments.after), rubric)
            comparison = comparison_payload(before, after)
            output_dir = arguments.output_dir.resolve()
            output_dir.mkdir(parents=True, exist_ok=True)
            markdown_path = output_dir / "agent-readiness-comparison.md"
            json_path = output_dir / "agent-readiness-comparison.json"
            html_path = output_dir / "agent-readiness-comparison.html"
            markdown_path.write_text(render_comparison_markdown(comparison), encoding="utf-8")
            write_json(json_path, comparison)
            html_path.write_text(render_comparison_html(comparison), encoding="utf-8")
            pdf_path = output_dir / "agent-readiness-comparison.pdf"
            if arguments.pdf:
                render_pdf(html_path, pdf_path, arguments.browser)
            print(
                f"Owned delta: {signed(comparison['summary']['owned_percentage_delta'])} points. "
                f"Regressions: {comparison['summary']['regression_count']}."
            )
            print(f"Markdown: {markdown_path}")
            print(f"JSON: {json_path}")
            print(f"HTML: {html_path}")
            if arguments.pdf:
                print(f"PDF: {pdf_path}")
        elif arguments.command == "doctor":
            report = doctor_report(arguments.repo, rubric)
            if arguments.as_json:
                print(json.dumps(report, indent=2))
            else:
                for check in report["checks"]:
                    print(f"{check['status'].upper():4}  {check['name']}: {check['detail']}")
            if not report["ok"]:
                return 1
        elif arguments.command == "vendor":
            plan = vendor_plan(arguments.target)
            if arguments.apply:
                apply_vendor_plan(plan)
            if arguments.as_json:
                output = dict(plan)
                output["mode"] = "apply" if arguments.apply else "dry-run"
                print(json.dumps(output, indent=2))
            else:
                print(f"Mode: {'apply' if arguments.apply else 'dry-run'}")
                print(f"Target: {plan['target']}")
                for item in plan["files"]:
                    print(f"{item['status'].upper():7} {item['path']}")
                if plan["previously_managed_but_retained"]:
                    print("Retained obsolete managed files: " + ", ".join(plan["previously_managed_but_retained"]))
                if not arguments.apply:
                    print("No files changed. Re-run with --apply after reviewing this plan.")
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
