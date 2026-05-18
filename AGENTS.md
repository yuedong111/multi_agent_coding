# Harness Agent Team Instructions

These rules apply to every agent in this harness.

## Operating Model

- Work only inside the target project root.
- Return exactly one JSON tool action per turn.
- Inspect existing files before creating or replacing code.
- Prefer the smallest change that satisfies the current task.
- Keep task status and agent messages concise and factual.
- Finish when your assigned role is complete; do not continue doing another agent's job unless it is necessary to unblock your role.

## Skill Use

- The prompt lists all available skills by name and description.
- Default skills are already loaded for the current agent.
- When a listed skill is relevant but not loaded, call `load_skill` before applying it.
- Do not invent skill names or assume a skill's body from its description.
- If a needed skill is unavailable, continue with the best local reasoning and mention the gap in your final summary.

## File Boundaries

- Do not edit files outside the target project root.
- Do not rewrite unrelated files.
- Do not delete user work unless the task explicitly requires it.
- Do not perform broad formatting churn unless formatting is the requested task.
- Do not change generated state directories such as `.git`, `.tasks`, `.team`, `.harness`, `.venv`, `__pycache__`, or dependency folders unless a harness tool owns that state.
- In refine mode, stay within the requested file scope unless a dependency file must change to keep the project working.

## Command Boundaries

- Run commands only from the target project root.
- Prefer read-only inspection commands before write or install commands.
- Do not run destructive commands such as hard resets, recursive deletes, disk wipes, or force pushes.
- Do not start long-running servers unless the task requires runtime verification.
- Do not print secrets, tokens, private keys, or environment values.
- Do not install new dependencies unless they are necessary and consistent with the existing project.

## Quality Bar

- Keep implementation, tests, docs, and release notes aligned.
- When changing behavior, add or update focused tests where practical.
- Treat failing tests, lint failures, or missing verification as risks to report.
- Code review agents should prioritize bugs, regressions, security issues, and missing tests over style preferences.
- Release agents should document how to run, verify, and deploy the result without overstating certainty.
