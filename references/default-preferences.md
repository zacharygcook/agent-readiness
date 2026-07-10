# Default Preferences

- Optimize for safer, faster autonomous engineering, not maximum keyword coverage.
- Use the owned applicability score as the primary score and show compatibility second.
- Target Level 5 by default; require an explicit percentage target for longer remediation loops.
- Prefer fixes with operational or developer payoff. Reject UI, dependencies, or infrastructure
  whose only purpose is satisfying the rubric.
- Require implementation, documentation/discoverability, automated validation, and behavior tests
  when they are proportionate to the criterion.
- Keep AGENTS.md concise. Put durable detail in domain documentation and link it once.
- For remediation loops, make one criterion-sized commit at a time and preserve unrelated work.
- Do not mutate external services merely to improve a score without explicit authorization.
- Do not reinterpret `not_applicable` as failure in the owned score.
