---
name: testing
description: Testing and verification workflow for generated or modified code.
---

# Testing

Test strategy:

1. Identify the project stack from files.
2. Add focused tests for user-visible behavior.
3. Run the smallest relevant command first.
4. If tests fail, read the failure and fix the cause.
5. If no test runner exists, create a lightweight smoke check.

Always record:

- command
- pass/fail
- important error
- remaining risk
