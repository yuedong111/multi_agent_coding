---
name: code-review
description: Code review checklist focused on bugs, regressions, missing tests, and behavior risk.
---

# Code Review

Review priority:

1. Correctness bugs.
2. Security or data loss risk.
3. Broken interfaces or regressions.
4. Missing tests for changed behavior.
5. Maintainability issues that will soon hurt.

Do not spend review budget on style preferences unless they hide a real issue.
When you find a problem, give the exact file, behavior, and fix direction.
