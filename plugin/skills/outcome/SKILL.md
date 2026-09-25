---
name: outcome
description: Close the loop on a forgent-planned task by reporting whether it succeeded, so future plans and routing learn from it.
argument-hint: "[success|failure] [note]"
---

Report the outcome of the current forgent session. Arguments: $ARGUMENTS

1. Find the session id from the most recent forgent plan card in this conversation. If there is none, call `memory_view` on `/sessions/` and pick the latest session whose task matches the work just done.
2. Decide success: use the argument if one was given; otherwise judge it against the card's success criteria and say which ones were met.
3. Call `report_outcome` with the session id, `success`, and a one-line note naming what worked or what blocked.
4. If something non-obvious was learned, also save it with `memory_write` under `/notes/<topic>`.
