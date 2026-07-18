---
name: agent-readiness-scoring
description: Audit, score, compare, report, and iteratively improve how safely and effectively coding agents can work in a software repository. Use for read-only agent-readiness audits, first-class HTML/PDF readiness reports, readiness levels or percentages, Factory-compatible comparisons, AGENT_READINESS_PREFERENCES.md setup, selecting remediations, or autonomous one-criterion-at-a-time improvement loops.
---

# Agent Readiness Scoring

Produce a personally owned, vendor-neutral readiness assessment from a transparent 82-criterion
rubric. Prefer real engineering capability over score theater and make every judgment auditable.

## Choose the operation

- **Audit-report:** inspect and score without changing the repository, then generate HTML, PDF when
  local Chromium is available, Markdown, and JSON artifacts.
- **Initialize preferences:** copy `assets/DEFAULT_AGENT_READINESS_PREFERENCES.md` to
  `AGENT_READINESS_PREFERENCES.md` in the repository root only when the user requests it or approves
  repository changes. Never overwrite an existing file.
- **Remediate-one:** score, select one failing criterion, implement a durable repo-specific fix,
  validate it, rescore it, and commit only that fix when authorized.
- **Improve-to-target:** repeat one criterion and one commit at a time until the requested owned
  percentage or level is reached, or a genuine blocker requires user authority.
- **Compare:** compare two assessments or reports and make regressions visible even when the total
  score rises.

For audit-report or compare, read `references/rubric.json` and `references/report-workflow.md`
completely. For remediate-one or improve-to-target, also read `references/remediation-loop.md`.
Apply preferences in this order: explicit instructions in the
current request, root `AGENT_READINESS_PREFERENCES.md`, then
`assets/DEFAULT_AGENT_READINESS_PREFERENCES.md`. State which file was used. Preferences guide how to
implement a capability; they are not standing permission to create or connect third-party accounts,
accept costs, install external apps, add secrets, or mutate production.

## Audit workflow

1. Read repository instructions and preferences before evaluating anything.
2. Record the current commit and dirty-tree state. Audits are read-only.
3. Discover deployable/runnable applications from source, manifests, workspace configuration, and
   deployment files. Libraries are applications only when independently built, tested, or shipped.
4. Evaluate all 82 criteria. Repository criteria receive one judgment. Application criteria receive
   one judgment per application: `pass`, `fail`, or `not_applicable`.
5. Use `not_applicable` only for skippable criteria and explain why that application is outside the
   criterion's actual risk surface. Never infer failure merely from inapplicability.
6. Require concrete evidence for every pass. Prefer source/config paths and successful commands;
   external-state criteria may cite CLI/API output. Do not award credit for prose claiming an
   implementation exists when the implementation is absent.
7. Create an assessment matching `references/assessment-format.md`.
   Record command and external-state checks in `provenance.evidence_checks`; store concise summaries,
   timestamps, and exit status rather than secrets or raw output.
8. Add repository-aware recommendations following `references/report-workflow.md`, then validate and
   render it with:

   `python3 <skill-dir>/scripts/readiness.py score --assessment <assessment.json> --output-dir <dir> --pdf`

9. Report both scores:
   - **Owned score:** excludes inapplicable applications from each criterion denominator.
   - **Compatibility score:** counts mixed inapplicable applications against app-scoped criteria,
     reproducing the vendor behavior for comparison. Fully inapplicable criteria remain skipped.
10. Lead with level, percentage, failed criteria, and highest-value next actions. Link the generated
    HTML, Markdown, and JSON reports.

If subagents are available and the task benefits from independence, give a fresh auditor only the
repository path and: `Use $agent-readiness-scoring at <skill-dir> to perform a read-only audit.` Do
not leak expected scores. The primary agent must still validate the resulting assessment.

## Scoring integrity

- Keep the compatibility rubric stable; version intentional rubric changes.
- Owned extensions are versioned, evidence-backed checkpoints outside the 82-criterion rubric.
  Report their denominators separately and never blend them into compatibility scoring.
- Weight every non-skipped criterion equally. For app-scoped criteria, the criterion score is the
  fraction of applicable apps passing, not a raw point total.
- Level bands match the compatibility baseline: Level 1 `<20%`, Level 2 `20–<40%`, Level 3
  `40–<60%`, Level 4 `60–<80%`, Level 5 `80–100%`.
- A documented policy may satisfy documentation criteria, but cannot substitute for runnable
  tooling in implementation criteria.
- A generated report, script, or dashboard must create operational value beyond influencing a
  scorer. If it does not, score it as a failure even if a keyword-based evaluator might pass it.
- Preserve negative findings. Never rewrite evidence or applicability solely to hit a target.
- Honor preference overrides only when they make the standard clearer or stricter. Record overrides
  in the assessment so results remain comparable.

## Deterministic tools

- `readiness.py init`: create an unscored 82-criterion assessment skeleton with an empty owned-extension map.
- `readiness.py validate`: validate IDs, scopes, statuses, evidence, and application coverage.
- `readiness.py score`: validate and generate HTML, Markdown, and JSON readiness reports.
  Add `--pdf` for a Chromium-derived PDF and `--previous` to embed progress from the prior round.
- `readiness.py compare`: compare two assessments or report JSON files and generate Markdown, JSON,
  and HTML deltas with regressions first.
- `readiness.py doctor`: verify package integrity, tools, Git state, preference discovery, and
  vendored-package fingerprints.
- `readiness.py vendor`: preview a deterministic package sync; require `--apply` to write only the
  explicit distributable files and retain unrelated files.
- `readiness.py list`: print the rubric in a compact table.
- `readiness.py preferences`: copy the preferences template without overwriting.

Run `python3 <skill-dir>/scripts/readiness.py --help` for arguments. Keep working assessment files in
gitignored local notes unless the repository preferences explicitly request committed reports.
