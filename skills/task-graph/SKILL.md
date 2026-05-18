---
name: task-graph
description: Persistent task graph rules for dependency-aware agent work.
---

# Task Graph

Tasks have:

- `id`
- `subject`
- `description`
- `status`
- `blockedBy`
- `blocks`
- `owner`

Rules:

1. Break large goals into a small number of meaningful tasks.
2. Use dependencies only when one task truly cannot start before another.
3. Update task status through `pending -> in_progress -> completed`.
4. When blocked, explain the blocker in the task description or result.
5. Prefer 4 to 8 tasks for a normal feature.
