# Assessment Format

Create UTF-8 JSON with this shape:

```json
{
  "schema_version": "1.0",
  "rubric_version": "1.0",
  "repository": {
    "name": "example",
    "path": "/absolute/path",
    "commit": "full git SHA",
    "generated_at": "2026-07-10T12:00:00Z",
    "dirty": false,
    "applications": {
      "backend": {"path": "backend", "description": "HTTP API and workers"}
    }
  },
  "preferences": {
    "source": "AGENT_READINESS_PREFERENCES.md",
    "checksum": "sha256 hex digest",
    "overrides": []
  },
  "provenance": {
    "audit_timestamp": "2026-07-10T12:00:00Z",
    "rubric_checksum": "sha256 hex digest",
    "preferences_checksum": "sha256 hex digest",
    "skill_version": "0.2.0",
    "skill_fingerprint": "sha256 hex digest",
    "skill_source_commit": "full git SHA or null",
    "repository_commit": "full git SHA",
    "repository_dirty": false,
    "applications": ["backend"],
    "evidence_checks": [
      {
        "kind": "command",
        "label": "backend tests",
        "checked_at": "2026-07-10T12:05:00Z",
        "exit_status": 0,
        "command": "yarn workspace backend test",
        "summary": "All backend tests passed."
      },
      {
        "kind": "external",
        "label": "branch protection",
        "checked_at": "2026-07-10T12:06:00Z",
        "fresh_until": "2026-07-17T12:06:00Z",
        "exit_status": 0,
        "summary": "Required checks are enabled on the default branch."
      }
    ]
  },
  "recommendations": [
    {
      "criterion_id": "lint_config",
      "priority": 1,
      "rationale": "A shared lint command removes a common source of unsafe agent handoffs.",
      "effort": "small",
      "authority": "autonomous",
      "flags": []
    },
    {
      "criterion_id": "distributed_tracing",
      "priority": 2,
      "rationale": "Tracing would close a production diagnostic gap but requires a vendor and architecture decision.",
      "effort": "large",
      "authority": "approval_required",
      "flags": ["paid_service", "large_refactor"]
    }
  ],
  "owned_extensions": {
    "build_deploy_performance_hygiene": {
      "version": "1.0",
      "controls": {
        "phase_timing": {"status":"pass","rationale":"Build and deploy phases are recorded.","evidence":["scripts/build_performance_report.mjs — writes phase timing evidence"],"confidence":"high"},
        "service_triggers": {"status":"pass","rationale":"Each service has explicit trigger boundaries.","evidence":[".github/workflows/deploy-worker.yml — scoped paths"],"confidence":"high"},
        "artifact_boundaries": {"status":"pass","rationale":"Runtime images exclude development-only artifacts.","evidence":["Dockerfile — multi-stage runtime target"],"confidence":"high"},
        "cache_limits": {"status":"pass","rationale":"Builder cache retention has an enforced cap.","evidence":["docker/buildkitd.toml — maxUsedSpace"],"confidence":"high"},
        "regression_budgets": {"status":"pass","rationale":"Measured image sizes have warning and failure budgets.","evidence":["docs/deployment/build_performance_budgets.json — thresholds"],"confidence":"high"}
      }
    }
  },
  "criteria": {
    "readme": {
      "status": "pass",
      "rationale": "Root README documents setup and usage.",
      "evidence": ["README.md:20 — setup", "README.md:55 — usage"],
      "confidence": "high"
    },
    "database_schema": {
      "applications": {
        "backend": {
          "status": "pass",
          "rationale": "Tracked migrations and ORM models define the database.",
          "evidence": ["backend/database/migrations — tracked migrations"],
          "confidence": "high"
        },
        "frontend": {
          "status": "not_applicable",
          "rationale": "The frontend has no persistence/database surface.",
          "evidence": ["frontend/package.json — no database client"],
          "confidence": "high"
        }
      }
    }
  }
}
```

Include exactly the 82 rubric IDs. Repository-scoped entries use a direct judgment. Application-
scoped entries use `applications` and include every discovered application exactly once.

Allowed statuses are `pass`, `fail`, and `not_applicable`; the last is valid only for skippable
criteria. Allowed confidence values are `high`, `medium`, and `low`. A pass requires at least one
evidence item. Evidence should be a concise path/command plus what it proves, never a bare keyword.

`provenance` is optional for legacy assessments and created automatically by `readiness.py init`.
For a new audit, preserve it and append every command or external-state check actually used. Keep
summaries under 500 characters, commands on one line, and never store secrets or large raw output.
Give external checks a `fresh_until` timestamp when their truth can change; stale evidence is then
surfaced in generated reports.

`recommendations` is optional for legacy assessments. New audits should provide three to eight when
failures exist. Use unique positive priorities, known criterion IDs, a concise repository-specific
rationale, `small`/`medium`/`large` effort, and `autonomous`/`approval_required`/`deferred` authority.
Allowed flags are `paid_service`, `external_account`, `large_refactor`, and `production_change`.
Recommendations guide the report; they do not grant implementation permission.

## Owned extensions

`owned_extensions` is optional and does not alter the stable 82-criterion owned or compatibility
scores. It records additional, versioned repository standards with their own denominator. The
currently supported `build_deploy_performance_hygiene` checkpoint has exactly five evidence-backed,
non-skippable controls: `phase_timing`, `service_triggers`, `artifact_boundaries`, `cache_limits`,
and `regression_budgets`. A checkpoint passes only when every listed control passes; prose alone is
not a valid substitute for a control judgment and its evidence.
