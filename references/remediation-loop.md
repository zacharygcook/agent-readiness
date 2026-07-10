# Remediation Loop

## Selection

1. Start from a validated fresh assessment.
2. Filter to failing, applicable criteria at or below the requested target level.
3. Apply repository preference priorities and exclusions.
4. Rank by real payoff, risk reduction, criterion dependencies, implementation effort, and confidence
   that the evidence is complete. Do not blindly pick the cheapest point.
5. Announce the selected criterion, current evidence, intended capability, and validation boundary.

## Implementation

1. Read relevant repository docs, history, source, tests, and deployment configuration.
2. Check whether work already exists locally, in commits, or on another authorized workstation.
3. Design the smallest durable capability that satisfies the criterion for the actual risk surface.
4. Add discoverability and automated enforcement without bloating AGENTS.md.
5. Add behavior-focused tests. Do not retain temporary proof tests or implementation-detail tests.
6. Run targeted validation, then repository validation proportional to risk.
7. Re-evaluate the criterion from evidence. A green test alone does not force a pass.

## Autonomous scope and approval gates

Remediate aggressively inside the repository. Unless repository instructions say otherwise, agents
may autonomously create or change source, tests, documentation, configuration, scripts, dependencies,
GitHub Actions workflows, and other repository-owned CI automation. Do not stop merely because a
capability runs in CI or uses GitHub's built-in token.

Stop for explicit approval before:

- creating or connecting a third-party account, installing a GitHub or CI app, granting vendor
  access, adding an external-service secret, accepting paid terms, or creating recurring spend;
- mutating production, repository settings, organization settings, or another live external control
  plane beyond the authority already granted in the current request;
- undertaking a large architectural refactor or threading new production behavior through a broad
  call-site surface merely to satisfy a criterion; or
- adding or materially expanding cross-cutting metrics, distributed tracing, profiling, product
  analytics, error tracking, or similar instrumentation, especially through paid services such as
  Sentry or PostHog.

Repository-hosted GitHub workflow files are normal autonomous changes. Installing a GitHub App,
adding repository or organization secrets, or changing GitHub settings is approval-gated. Never add
an inferior free substitute, fake implementation, or score-only scaffold when the legitimate
capability requires a vendor decision or spend; leave the criterion failing or deferred instead.

When approval is required, first inspect the repository and present a concise decision brief with
the gap, recommended approach, reasonable alternatives including deferral, expected code surface,
cost/account/privacy implications, and the exact authority needed to continue.

## Failure notifications

When repository preferences enable email for important failures, prefer a high-signal notification
for scheduled or autonomous workflows that require human attention. Reuse an existing approved mail
provider and configuration. Send polished, accessible HTML with a useful plain-text fallback, a
specific subject, the failure impact, relevant evidence, and the next action. Do not email every
ordinary check failure or create a new account, secret, or paid service without approval.

## Commit boundary

When the user authorizes commits, stage explicit paths only and create one descriptive commit for
the criterion. Include the problem, capability, affected areas, validation, and deferred risks.
Never sweep unrelated dirty-tree files into the commit.

## Loop control

After each commit, regenerate the report from the new commit. Stop when the owned target is reached,
the user stops the loop, the next action needs new authority, or a blocker repeats without a safe
alternative. Do not broaden permissions because the target says “keep going.”
