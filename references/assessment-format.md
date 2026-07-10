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
    "overrides": []
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
