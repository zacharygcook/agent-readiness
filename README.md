# Agent Readiness

[![CI](https://github.com/zacharygcook/agent-readiness/actions/workflows/ci.yml/badge.svg)](https://github.com/zacharygcook/agent-readiness/actions/workflows/ci.yml)

A vendor-neutral skill that audits how safely and effectively coding agents can work in a repository,
then helps improve the gaps with evidence instead of score theater.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="assets/readiness-loop-dark.svg">
  <img alt="Readiness loop: audit a repository, gather evidence, score it, choose one authorized improvement, validate and rescore, then repeat until the target is met." src="assets/readiness-loop.svg">
</picture>

## Install

```bash
npx skills add zacharygcook/agent-readiness
```

## Start here

```text
$agent-readiness audit this repo and suggest improvements
```

The audit inspects the repository, scores each applicable criterion, and produces evidence-backed
HTML, Markdown, and JSON reports (plus a PDF when Chrome or Chromium is available). It does not
modify the repository.

```text
$agent-readiness walk me through setting up my preferences
```

This explains the repository's readiness preferences and creates `AGENT_READINESS_PREFERENCES.md`
only when you request or approve that change.

```text
$agent-readiness improve this repo to Level 4, one criterion at a time
```

The skill selects one meaningful gap, implements and validates a durable fix, then rescores before
moving to the next one. It stops when work needs new authority.

## What the score means

The rubric has 82 equally weighted criteria: 44 repository-scoped and 38 application-scoped. It
reports two views from the same evidence:

- **Owned score** excludes applications that are genuinely outside a criterion's risk surface.
- **Compatibility score** preserves the Factory baseline for comparisons.

| Level | Score |
|---:|---:|
| 1 | Below 20% |
| 2 | 20% to below 40% |
| 3 | 40% to below 60% |
| 4 | 60% to below 80% |
| 5 | 80% to 100% |

## Safety boundary

Audits and comparisons are read-only. Preferences guide implementation but do not grant standing
permission to create accounts, incur costs, add secrets, install external software, or change
production. Improvement work proceeds one criterion at a time and requests authority for those
actions when needed.

For deterministic CLI commands, run:

```bash
python3 scripts/readiness.py --help
```
