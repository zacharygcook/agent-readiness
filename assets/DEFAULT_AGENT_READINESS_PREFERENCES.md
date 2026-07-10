# Agent Readiness Preferences

This optional repository policy guides how readiness improvements should be implemented. It is not
standing permission to create accounts, accept paid terms, add external-service secrets, install
vendor apps, or mutate production.

## Targets

- Primary score: owned applicability score
- Target level: 5
- Target percentage: 100%

## Principles

- Build real capability, not score-only artifacts.
- Prefer improvements with measurable product, operational, security, or developer payoff.
- Treat an inapplicable application as outside that criterion's denominator.
- Require concrete source, configuration, or command evidence for every passing judgment.
- Keep AGENTS.md concise; link durable detail from domain documentation.

## Autonomous Remediation

- Agents may implement repository-owned code, tests, docs, scripts, configuration, dependencies,
  GitHub Actions workflows, and CI checks without asking solely because they run in CI.
- Prefer one criterion-sized commit and preserve unrelated work.
- Run targeted validation plus the repository's normal required checks.

## Ask First

- Creating or connecting a third-party account, installing an external app, adding a service secret,
  accepting paid terms, or introducing recurring spend.
- Mutating production or live repository, organization, deployment, or vendor settings.
- Large architectural refactors or broad new production call-site changes.
- Materially expanding metrics, tracing, profiling, analytics, or error-tracking instrumentation.

## Providers And Tools

| Capability | Preferred or existing tool | Repository-specific guidance |
|---|---|---|
| Add rows for approved tools and services here. |  |  |

Do not install a low-quality substitute merely to satisfy a criterion when the preferred solution
requires a vendor decision or spend.

## Important Failure Notifications

- Email important, actionable scheduled or autonomous workflow failures only when this repository
  has an approved provider and explicitly opts in below.
- Use polished, accessible HTML plus a useful plain-text fallback.
- Do not create a new account, secret, or paid service to enable notifications without approval.
- Repository opt-in and provider: add here.

## Repository Priorities And Deferrals

- Add repo-specific priorities here.
- Add criteria that should be deferred or excluded here, with reasons.

## Applicability Overrides

| Criterion | Application | Applicable? | Reason |
|---|---|---:|---|

## Criterion Overrides

| Criterion | Stricter local pass condition or implementation preference | Reason |
|---|---|---|
