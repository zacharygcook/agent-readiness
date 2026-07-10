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
