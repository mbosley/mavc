# Model adapters

A model operation declares Pydantic `Input` and `Output` classes and receives a callable through `Operation(caller=adapter)`. MAVC supplies a request with `node` (effective position settings), `system_prompt` (the operation docstring), and `prompt` (JSON containing schemas and input). Each model position must declare `provider`, `model`, and `reasoning`; these are arbitrary identifiers understood by your adapter. MAVC selects no provider or account for you.

The adapter returns a dictionary. For a JSON response, supply `text` containing a single JSON value and `stop_reason: "stop"`. MAVC rejects duplicate JSON keys and validates the value against `Output`. For terminal tool output, a custom renderer may set `terminal_tool`; return the accepted `proposal`. An operation may override `render(input, settings, children)` and `parse(request, response, input, children)`.

Return observations so MAVC can compare declared and executed settings:

- `observed.model`, `observed.reasoning`, `observed.provider` and `observed.active_tools` describe the executed call.
- `actual_requests` lists request payloads, each with `model` and `reasoning: {"effort": ...}`. `observed.request` captures the effective request when individual payloads are unavailable.
- `upstream_response` may include `model` and `status: "completed"`; `observed.response.status` may contain the HTTP status.
- Optional `error`, nonzero `exit_code`, incomplete upstream status, mismatched settings and missing required model/reasoning observations prevent caching an answer.

Missing optional observations are recorded as `unobserved`, not assumed correct. See [the offline example](../examples/offline.py) for a minimal executable response. No live provider integration is tested in this release.

`settings`, `request`, `pi_settings`, `pi_retry`, and the explicit `legacy` boundary preserve adapter-specific configuration. The Pi-named fields are compatibility fields; they do not install or invoke Pi. The default effective settings request no tools, no compaction or cache warming, SSE transport, and no nested retries. An adapter must implement those controls or report their observations as unavailable; record inspection shows what was actually checked. MAVC does not enforce a network timeout inside an arbitrary Python callable.

The prompt budget counts the system prompt plus the input prompt. A custom renderer whose adapter repeats the system prompt can set `system_prompt_copies`. A different `prompt_budget_chars` requires `prompt_budget_reason`. For growing corpora, use an index and tools to retrieve bounded input.

Tool operations bind their manifest with `bind_tools` and `bind_caller`. Quote matching is application-owned; no quote-check dependency or helper is bundled. Rejecting an invalid answer remains the flow author's responsibility, using checks and exceptions such as `Rejected` or `NeedsHumanReview`.
