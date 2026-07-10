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

## Commit boundary

When the user authorizes commits, stage explicit paths only and create one descriptive commit for
the criterion. Include the problem, capability, affected areas, validation, and deferred risks.
Never sweep unrelated dirty-tree files into the commit.

## Loop control

After each commit, regenerate the report from the new commit. Stop when the owned target is reached,
the user stops the loop, the next action needs new authority, or a blocker repeats without a safe
alternative. Do not broaden permissions because the target says “keep going.”
