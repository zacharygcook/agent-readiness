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
- Generates first-class, self-contained HTML, Markdown, and JSON reports through a dependency-free
  Python CLI, with optional PDF printing through an already-installed Chrome or Chromium browser.
- Visualizes category health, pass/partial/fail counts, application-level matrices, ranked next
  actions, complete evidence, and provenance in one editorial report.
- Compares scoring rounds with regressions, evidence changes, confidence changes, and applicability
  changes called out explicitly.
- Records portable package fingerprints and audit provenance so results and vendored copies can be
  verified later.
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
├── VERSION
├── SKILL.md
├── agents/openai.yaml
├── assets/DEFAULT_AGENT_READINESS_PREFERENCES.md
├── references/
│   ├── assessment-format.md
│   ├── report-workflow.md
│   ├── remediation-loop.md
│   └── rubric.json
├── evals/scenarios.json
└── scripts/
    ├── readiness.py
    ├── agent_eval.py
    ├── test_readiness.py
    └── test_agent_eval.py
```

`SKILL.md` contains the agent workflow. The rubric and remediation details are loaded only when
needed. `report-workflow.md` owns report-only behavior while `remediation-loop.md` owns implementation
behavior. `readiness.py` owns deterministic validation, scoring, comparison, report generation, and
package lifecycle checks. The separate `agent_eval.py` is maintainer-facing behavioral test tooling;
it is not part of ordinary repository scoring.

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

Generate HTML, Markdown, and JSON:

```bash
python3 scripts/readiness.py score --assessment /tmp/assessment.json --output-dir /tmp/readiness-report
```

Also generate a PDF from the exact HTML using an installed Chrome or Chromium browser:

```bash
python3 scripts/readiness.py score --assessment /tmp/assessment.json --output-dir /tmp/readiness-report --pdf
```

Embed progress from a previous assessment or report:

```bash
python3 scripts/readiness.py score --assessment /tmp/assessment.json --previous /tmp/previous-report.json --output-dir /tmp/readiness-report --pdf
```

Compare two assessment or report JSON files:

```bash
python3 scripts/readiness.py compare --before /tmp/before.json --after /tmp/after.json --output-dir /tmp/readiness-comparison
```

Check package health and preference discovery for a repository:

```bash
python3 scripts/readiness.py doctor --repo /absolute/path/to/repo
```

Initialize repository-specific preferences without overwriting an existing file:

```bash
python3 scripts/readiness.py preferences --output /absolute/path/to/repo/AGENT_READINESS_PREFERENCES.md
```

The CLI requires Python 3 and Git and has no third-party Python dependencies. PDF output additionally
requires Chrome or Chromium; use `--browser PATH` when auto-discovery cannot find it. The skill never
downloads a browser automatically. Both `score` and `compare` use the same self-contained HTML as the
PDF source, so there is no second visual template to drift.

## Using The Skill With An Agent

Example read-only report:

```text
Use $agent-readiness-scoring in audit-report mode. Do not modify the repository. Produce an
evidence-backed HTML and PDF report, show the owned and compatibility scores, and rank the
highest-value next improvements with effort and authority labels.
```

Example autonomous improvement loop:

```text
Use $agent-readiness-scoring in improve-to-target mode. Read AGENT_READINESS_PREFERENCES.md, then fix
one failing criterion at a time with full validation and one scoped commit per capability. Rescore,
embed progress from the previous round, and regenerate the HTML/PDF report after each commit. Continue
until the owned score reaches 94% or the next action requires new authority.
```

This remains one skill with four explicit operations: `audit-report`, `compare`, `remediate-one`, and
`improve-to-target`. The shared rubric, preferences, evidence contract, and renderer remain canonical;
only the operation-specific reference is loaded.

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

Scripts help when the work is deterministic and repeated. The CLI handles assessment initialization,
validation, scoring, report rendering, comparison, package diagnostics, safe vendoring, rubric
listing, and preference initialization.

Repository interpretation remains agent work: discovering applications, deciding applicability,
evaluating evidence, and choosing a durable remediation all depend on context. Encoding those as
keyword scanners or one brittle detector per criterion would make the score easier to game and less
portable across languages.

The design stays intentionally split: one stable user-facing CLI for deterministic mechanics, and a
separate maintainer-facing behavioral evaluator for model-driven forward tests.

## Testing

Run the complete deterministic suite:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_readiness.py --verbose
PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_agent_eval.py --verbose
```

The suite covers rubric integrity, score math, applicability, level boundaries, malformed inputs,
preference copying, report output, and end-to-end CLI behavior against temporary real Git
repositories. GitHub Actions runs the same suite and executable CLI smoke tests on every push and
pull request.

The native Droid audit transcript was converted into behavioral invariants rather than copied into
fixtures. Prepare an isolated scenario with `python3 scripts/agent_eval.py prepare --scenario
read-only-evidence-audit --output /tmp/readiness-eval`, give the generated prompt to a fresh agent,
then grade its run artifact with `agent_eval.py grade`. The evaluator verifies read-only integrity,
application discovery, evidence-backed negatives, preference handling, approval gates, safe CI
autonomy, and resistance to score-gaming.

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
`.gitignore`, deterministic tests, and behavioral eval harness are not part of the vendored skill
package.

Preview an exact sync without changing anything:

```bash
python3 scripts/readiness.py vendor --target /absolute/path/to/repo/.agents/skills/agent-readiness-scoring
```

After reviewing the file-level plan, add `--apply`. The command writes only the explicit
distributable files, records their checksums in `.agent-readiness-package.json`, validates the result,
and retains unrelated or formerly managed files rather than deleting them.

## Current Status

- Transparent rubric: 82 criteria, version 1.0
- Package version: 0.3.0 with portable SHA-256 fingerprinting
- Deterministic suite: 32 scoring/package tests plus 4 behavioral-harness tests
- Reports: editorial HTML and Chromium-derived PDF plus Markdown and JSON
- Report contents: category bars, application matrix, authority-aware actions, progress, evidence,
  and provenance
- Comparisons: regression-first HTML/PDF, Markdown, and JSON
- Default branch: `master`
- Repository visibility: private

This is an actively evolving personal engineering system. Compatibility changes to the baseline
rubric should be intentional and versioned; owned reporting and workflow improvements can evolve more
quickly as long as evidence integrity is preserved.
