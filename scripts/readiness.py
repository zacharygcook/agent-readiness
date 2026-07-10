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
    "references/remediation-loop.md",
    "references/rubric.json",
    "scripts/readiness.py",
)
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
    category_scores: dict[str, list[float]] = defaultdict(list)
    for score in scores:
        if score.owned_ratio is not None:
            category_scores[definitions[score.criterion_id]["category"]].append(score.owned_ratio)
    category_summary = {
        category: round(sum(values) * 100 / len(values), 4) if values else 0.0
        for category in CATEGORY_TITLES
        for values in [category_scores.get(category, [])]
    }
    provenance = assessment.get("provenance", {})
    return {
        "schema_version": "1.0",
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
        },
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


def render_html(payload: dict[str, Any]) -> str:
    summary = payload["summary"]
    repository = payload["repository"]
    criteria = payload["criteria"]
    failures = [criterion for criterion in criteria.values() if criterion["owned"]["ratio"] not in {None, 1.0}]
    failures.sort(key=lambda criterion: (criterion["level"], criterion["title"]))
    category_cards = "".join(
        f'<article class="category"><span>{escape(CATEGORY_TITLES[key])}</span>'
        f'<strong>{value:.1f}%</strong><div class="bar"><i style="width:{max(0, min(100, value)):.2f}%"></i></div></article>'
        for key, value in summary.get("categories", {}).items()
    )
    failure_items = "".join(
        f'<li><div><strong>{escape(item["title"])}</strong> <code>{escape(identifier)}</code></div>'
        f'<span>Level {item["level"]} · failing: {escape(", ".join(item["failing_units"]))}</span>'
        f'<p>{escape(item["guidance"])}</p></li>'
        for identifier, item in ((identifier, criteria[identifier]) for identifier in criteria)
        if item in failures
    ) or "<li class=\"empty\">All applicable criteria pass.</li>"
    rows = "".join(
        f'<tr><td><strong>{escape(item["title"])}</strong><br><code>{escape(identifier)}</code></td>'
        f'<td>{escape(CATEGORY_TITLES[item["category"]])}</td><td>L{item["level"]}</td>'
        f'<td>{format_ratio(item["owned"]["ratio"])}</td>'
        f'<td>{escape(", ".join(item["failing_units"]) or "—")}</td></tr>'
        for identifier, item in criteria.items()
    )
    evidence_sections: list[str] = []
    for identifier, item in criteria.items():
        judgment_blocks: list[str] = []
        for unit, judgment in report_judgments(item).items():
            evidence = "".join(f"<li>{escape(value)}</li>" for value in judgment.get("evidence", []))
            judgment_blocks.append(
                f'<div class="judgment"><div><strong>{escape(unit)}</strong> '
                f'<span class="status {escape(judgment.get("status", "unknown"))}">{escape(judgment.get("status", "unknown"))}</span> '
                f'<span class="confidence">{escape(judgment.get("confidence", "unknown"))} confidence</span></div>'
                f'<p>{escape(judgment.get("rationale", ""))}</p><ul>{evidence}</ul></div>'
            )
        evidence_sections.append(
            f'<details><summary><span>{escape(item["title"])}</span><code>{escape(identifier)}</code></summary>'
            + "".join(judgment_blocks) + "</details>"
        )
    evidence_details = "".join(evidence_sections)
    warnings = payload.get("audit_warnings", [])
    warning_block = ""
    if warnings:
        warning_block = '<section class="warnings"><h2>Audit warnings</h2><ul>' + "".join(
            f"<li>{escape(warning)}</li>" for warning in warnings
        ) + "</ul></section>"
    generated = repository.get("generated_at", "unknown")
    commit = repository.get("commit", "unknown")
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Agent Readiness · {escape(repository.get('name', 'repository'))}</title>
<style>
:root{{--ink:#17141a;--muted:#69616f;--paper:#fffdfb;--panel:#f7f2f6;--accent:#f02d8b;--good:#14855f;--line:#e5dce4}}*{{box-sizing:border-box}}body{{margin:0;background:var(--paper);color:var(--ink);font:15px/1.5 ui-sans-serif,system-ui,-apple-system,sans-serif}}main{{max-width:1120px;margin:auto;padding:54px 28px 80px}}header{{display:grid;grid-template-columns:1fr auto;gap:24px;align-items:end;border-bottom:4px solid var(--ink);padding-bottom:28px}}.eyebrow{{color:var(--accent);font-weight:800;letter-spacing:.12em;text-transform:uppercase}}h1{{font-size:clamp(38px,7vw,76px);line-height:.95;letter-spacing:-.055em;margin:10px 0}}h2{{font-size:27px;letter-spacing:-.03em;margin:44px 0 16px}}.score{{font-size:72px;line-height:.8;font-weight:900;text-align:right}}.score small{{display:block;color:var(--muted);font-size:14px;letter-spacing:.08em;margin-top:18px}}.meta{{color:var(--muted);margin-top:14px}}.grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}}.category{{background:var(--panel);padding:18px;border-radius:12px}}.category span{{display:block;color:var(--muted);min-height:44px}}.category strong{{font-size:27px}}.bar{{height:5px;background:#dfd5de;margin-top:12px}}.bar i{{display:block;height:100%;background:var(--accent)}}ul.failures{{padding:0;list-style:none;display:grid;grid-template-columns:repeat(2,1fr);gap:12px}}.failures li,.warnings{{border:1px solid var(--line);padding:18px;border-radius:12px}}.failures span,.failures p,.confidence{{color:var(--muted)}}code{{background:#efe7ed;padding:2px 6px;border-radius:4px}}table{{width:100%;border-collapse:collapse;font-size:13px}}th,td{{padding:12px 10px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}}th{{position:sticky;top:0;background:var(--paper)}}.warnings{{border-color:#e5b64e;background:#fff9e9}}details{{border-bottom:1px solid var(--line);padding:13px 2px}}summary{{display:flex;justify-content:space-between;cursor:pointer;font-weight:750}}.judgment{{margin:14px 0;padding:14px 18px;background:var(--panel);border-radius:10px}}.judgment p{{margin:8px 0}}.judgment ul{{margin:6px 0}}.status{{display:inline-block;border-radius:999px;padding:2px 8px;margin-left:8px;font-size:11px;font-weight:800;text-transform:uppercase}}.status.pass{{background:#dff5eb;color:#075f41}}.status.fail{{background:#ffe1e8;color:#961338}}.status.not_applicable{{background:#eae5ea;color:#554e58}}footer{{margin-top:40px;color:var(--muted);font-size:12px}}@media(max-width:760px){{header{{grid-template-columns:1fr}}.score{{text-align:left}}.grid{{grid-template-columns:1fr 1fr}}ul.failures{{grid-template-columns:1fr}}}}@media print{{main{{padding:20px}}.category,.failures li{{break-inside:avoid}}}}
</style></head><body><main><header><div><div class="eyebrow">Agent readiness report</div><h1>{escape(repository.get('name', 'Repository'))}</h1><div class="meta">{escape(generated)} · <code>{escape(commit)}</code></div></div><div class="score">{summary['owned_percentage']:.1f}%<small>LEVEL {summary['owned_level']} · OWNED</small></div></header>
<p class="meta">Compatibility view: Level {summary['compatibility_level']} at {summary['compatibility_percentage']:.1f}% · {summary['applicable_criteria']} applicable criteria · {summary['skipped_criteria']} skipped.</p>
{warning_block}<h2>Category health</h2><section class="grid">{category_cards}</section><h2>Highest-value failures</h2><ul class="failures">{failure_items}</ul><h2>All criteria</h2><table><thead><tr><th>Criterion</th><th>Category</th><th>Level</th><th>Owned</th><th>Failing units</th></tr></thead><tbody>{rows}</tbody></table><h2>Evidence &amp; rationale</h2><section>{evidence_details}</section>
<footer>Generated by agent-readiness-scoring {escape(payload.get('provenance', {}).get('skill_version', 'legacy'))}. Self-contained and print-ready.</footer></main></body></html>"""


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
    init_parser.add_argument("--app", action="append", type=parse_application, default=[])
    init_parser.add_argument("--output", type=Path, required=True)

    validate_parser = subparsers.add_parser("validate", help="Validate a completed assessment.")
    validate_parser.add_argument("--assessment", type=Path, required=True)

    score_parser = subparsers.add_parser("score", help="Validate, score, and render a report.")
    score_parser.add_argument("--assessment", type=Path, required=True)
    score_parser.add_argument("--output-dir", type=Path, required=True)

    compare_parser = subparsers.add_parser("compare", help="Compare two assessments or report JSON files.")
    compare_parser.add_argument("--before", type=Path, required=True)
    compare_parser.add_argument("--after", type=Path, required=True)
    compare_parser.add_argument("--output-dir", type=Path, required=True)

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
            markdown_path.write_text(render_markdown(assessment, rubric, scores), encoding="utf-8")
            payload = report_payload(assessment, rubric, scores)
            write_json(json_path, payload)
            html_path.write_text(render_html(payload), encoding="utf-8")
            print(
                f"Owned: Level {payload['summary']['owned_level']} "
                f"({payload['summary']['owned_percentage']:.2f}%). "
                f"Compatibility: Level {payload['summary']['compatibility_level']} "
                f"({payload['summary']['compatibility_percentage']:.2f}%)."
            )
            print(f"Markdown: {markdown_path}")
            print(f"JSON: {json_path}")
            print(f"HTML: {html_path}")
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
            print(
                f"Owned delta: {signed(comparison['summary']['owned_percentage_delta'])} points. "
                f"Regressions: {comparison['summary']['regression_count']}."
            )
            print(f"Markdown: {markdown_path}")
            print(f"JSON: {json_path}")
            print(f"HTML: {html_path}")
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
