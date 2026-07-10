# Agent Readiness Scoring

[![CI](https://github.com/zacharygcook/agent-readiness-scoring/actions/workflows/ci.yml/badge.svg)](https://github.com/zacharygcook/agent-readiness-scoring/actions/workflows/ci.yml)

A personally owned, vendor-neutral skill for measuring and improving how safely coding agents can
work in a software repository.

It preserves the useful core of the Factory Agent Readiness model—a transparent 82-criterion rubric,
equal criterion weighting, and Level 1–5 bands—while making the evidence, applicability decisions,
preferences, reports, and remediation loop fully inspectable and customizable.

## What It Does

- Audits 44 repository-scoped and 38 application-scoped readiness criteria.
- Requires rationale, evidence, and confidence for every judgment.
- Produces a fair owned score that excludes genuinely inapplicable applications.
- Produces a Factory-compatible score beside it for comparison.
- Discovers and honors repository-specific `AGENT_READINESS_PREFERENCES.md` guidance.
- Supports one-criterion-at-a-time autonomous remediation with explicit safety gates.
- Generates Markdown and JSON reports through a dependency-free Python CLI.
- Supports custom 82-criterion rubric variants for other stacks and domains.

The project deliberately values real engineering capability over keyword coverage. Documentation can
satisfy documentation criteria; it cannot stand in for missing runtime tooling, tests, telemetry, or
automation.

## Scoring Model

Every applicable criterion has equal weight.

| Level | Score |
|---:|---:|
| 1 | Below 20% |
| 2 | 20% to below 40% |
| 3 | 40% to below 60% |
| 4 | 60% to below 80% |
| 5 | 80% to 100% |

Application-scoped criteria are scored as the fraction of applicable applications that pass.

- **Owned score:** excludes applications whose risk surface is genuinely inapplicable.
- **Compatibility score:** reproduces the baseline behavior where mixed inapplicability reduces an
  application-scoped criterion's score.

Both views come from the same evidence-backed assessment.

## Skill Layout

```text
agent-readiness-scoring/
├── SKILL.md
├── agents/openai.yaml
├── assets/DEFAULT_AGENT_READINESS_PREFERENCES.md
├── references/
│   ├── assessment-format.md
│   ├── remediation-loop.md
│   └── rubric.json
└── scripts/
    ├── readiness.py
    └── test_readiness.py
```

`SKILL.md` contains the agent workflow. The rubric and remediation details are loaded only when
needed. `readiness.py` owns deterministic validation, scoring, and report generation.

## Quick Start

List the rubric:

```bash
python3 scripts/readiness.py list
```

Create an assessment skeleton for a repository with backend and frontend applications:

```bash
python3 scripts/readiness.py init --repo /absolute/path/to/repo --app backend=backend --app frontend=frontend --output /tmp/assessment.json
```

Validate a completed assessment:

```bash
python3 scripts/readiness.py validate --assessment /tmp/assessment.json
```

Generate the report:

```bash
python3 scripts/readiness.py score --assessment /tmp/assessment.json --output-dir /tmp/readiness-report
```

Initialize repository-specific preferences without overwriting an existing file:

```bash
python3 scripts/readiness.py preferences --output /absolute/path/to/repo/AGENT_READINESS_PREFERENCES.md
```

The CLI requires Python 3 and Git. It has no third-party Python dependencies.

## Using The Skill With An Agent

Example read-only audit:

```text
Use $agent-readiness-scoring to audit this repository. Produce an evidence-backed report, show the
owned and compatibility scores, and recommend the highest-value next improvements. Do not modify the
repository.
```

Example autonomous improvement loop:

```text
Use $agent-readiness-scoring to audit this repository. Read AGENT_READINESS_PREFERENCES.md, then fix
one failing criterion at a time with full validation and one scoped commit per capability. Rescore
after each commit and continue until the owned score reaches 94% or the next action requires new
authority.
```

The skill can ask a fresh auditor for an independent read-only pass when the host supports agent
delegation. The primary agent must still validate the assessment and must not leak an expected score
to the auditor.

## Preferences And Authority

Preferences load in this order:

1. Explicit instructions in the current request
2. Repository-root `AGENT_READINESS_PREFERENCES.md`
3. Bundled `DEFAULT_AGENT_READINESS_PREFERENCES.md`

Preferences describe how a repository should be improved. They are not standing permission to
create accounts, accept paid terms, add external-service secrets, install vendor applications, or
mutate production.

Repository-owned source, tests, scripts, configuration, dependencies, and GitHub Actions workflows
are normally inside the autonomous remediation envelope. New spend, third-party access, live control
plane changes, invasive architecture work, and broad telemetry instrumentation require explicit
authority.

## Why Ship A CLI?

Scripts help when the work is deterministic and repeated. The CLI currently handles assessment
initialization, validation, scoring, report rendering, rubric listing, and preference initialization.

Repository interpretation remains agent work: discovering applications, deciding applicability,
evaluating evidence, and choosing a durable remediation all depend on context. Encoding those as
keyword scanners or one brittle detector per criterion would make the score easier to game and less
portable across languages.

The intended direction is one stable CLI with focused subcommands—not dozens of loosely related
scripts. Good future candidates include report comparison, package diagnostics, provenance checks,
and safe vendoring drift detection. Model-driven forward evaluation belongs in separate maintainer
tooling.

## Testing

Run the complete deterministic suite:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_readiness.py --verbose
```

The suite covers rubric integrity, score math, applicability, level boundaries, malformed inputs,
preference copying, report output, and end-to-end CLI behavior against temporary real Git
repositories. GitHub Actions runs the same suite and executable CLI smoke tests on every push and
pull request.

Deterministic tests are only the first layer. Skill behavior should also be forward-tested against
isolated synthetic repositories to verify read-only integrity, evidence quality, preference handling,
approval gates, and resistance to score-gaming.

## Vendoring

The canonical personal installation lives at:

```text
~/.agents/skills/agent-readiness-scoring
```

Repositories can vendor the distributable skill files under
`.agents/skills/agent-readiness-scoring/` and expose that directory to other agent surfaces through
their normal skill-discovery mechanism. Keep repository-specific preferences at the repository root;
do not edit the bundled default into a hidden project policy.

Repository-only infrastructure such as this canonical repository's root `.github/` workflow and
`.gitignore` is not part of the vendored skill package.

## Current Status

- Transparent rubric: 82 criteria, version 1.0
- Deterministic suite: 21 tests
- Reports: Markdown and JSON
- Default branch: `master`
- Repository visibility: private

This is an actively evolving personal engineering system. Compatibility changes to the baseline
rubric should be intentional and versioned; owned reporting and workflow improvements can evolve more
quickly as long as evidence integrity is preserved.
