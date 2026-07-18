# Report Workflow

Use this workflow for `audit-report` and `compare` operations. These operations are read-only except
for writing report artifacts to the user-approved output directory.

## Assessment to artifacts

1. Produce and validate one evidence-backed assessment JSON.
2. Add three to eight repository-aware `recommendations` when applicable. Rank real engineering
   value, dependency order, risk reduction, effort, and confidence rather than easy score points.
3. Label each recommendation with:
   - `effort`: `small`, `medium`, or `large`;
   - `authority`: `autonomous`, `approval_required`, or `deferred`; and
   - any relevant flags: `paid_service`, `external_account`, `large_refactor`, or
     `production_change`.
4. Render HTML, Markdown, and JSON. Request PDF when Chrome or Chromium is available:

   `python3 <skill-dir>/scripts/readiness.py score --assessment <assessment.json> --output-dir <dir> --pdf`

5. When a prior assessment or report exists, embed progress in the new report:

   `python3 <skill-dir>/scripts/readiness.py score --assessment <assessment.json> --previous <previous.json> --output-dir <dir> --pdf`

6. Link the HTML and PDF first, then Markdown and JSON. State the exact preferences source and
   whether the worktree was dirty at audit time.

## Report behavior

- Treat JSON as the source of truth and HTML as the canonical presentation.
- Derive PDF from the same HTML with Chromium print CSS; never maintain a second visual template.
- Show category percentages with pass, partial, fail, and inapplicable counts.
- Preserve application-level differences in the matrix.
- Keep regressions prominent even when the total score rises.
- Show mechanical fallback actions as unclassified when the assessment lacks agent-ranked
  recommendations; never imply a deterministic ranking has repository judgment.
- Include the complete evidence ledger and provenance in print output.
- When an assessment includes an owned extension, report its checkpoint and control denominators
  separately; never blend them into the stable 82-criterion owned or compatibility scores.

PDF generation is optional infrastructure, not permission to install software. If no browser is
available, deliver the HTML and other formats, explain the missing local dependency, and do not
download Chrome or Chromium automatically. Use `--browser PATH` when the user provides a browser.

Keep generated assessments and reports in gitignored local notes unless repository preferences ask
for committed artifacts. An audit-report must not change application code, configuration, Git state,
external services, or repository settings.
