---
name: plan
description: Get a forgent PlanCard for a task before starting non-trivial work (features, bug fixes, refactors, reviews, architecture). Returns steps, gotchas, success criteria, and a memory index for this project.
argument-hint: "<task description>"
---

Plan this task with forgent, then do it: $ARGUMENTS

1. Call forgent's `advise_task` tool with the task above (use the whole request if no task was given).
2. Show the plan card block from the result to the user.
3. Work through the plan's steps with your own tools. Treat the gotchas and success criteria as constraints.
4. Open memory index paths with `memory_view` only when one is relevant to the step you are on.
5. When you learn something a future session on this project should know (a file location, a convention, a trap), save it with `memory_write` under `/notes/<topic>`.
6. When the task ends, successfully or not, call `report_outcome` with the card's session id and a one-line note.
