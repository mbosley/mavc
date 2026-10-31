# Codex and Claude Code callers

These optional, standard-library callers invoke the agent CLI you already have. Install `.[codex]` or `.[claude-code]`; these extras add no Python dependencies and do not install or authenticate a CLI. The command contracts were checked against locally installed **Codex 0.164.0** and **Claude Code 2.1.310**, and tested with mocked subprocesses. No live/subscription model smoke test was performed.

Both implement the same `caller(request) -> response` interface as other MAVC adapters. Use the supplied subclasses to fingerprint their helper code:

```python
from pydantic import BaseModel, ConfigDict
from mavc.adapters.codex import Codex
# Or: from mavc.adapters.claude_code import ClaudeCode as Codex

class Label(Codex):
    """Label the supplied text with a short description."""
    class Input(BaseModel):
        text: str
    class Output(BaseModel):
        model_config = ConfigDict(extra='forbid')
        label: str
```

Run it with `Runner(..., flow=flow)` as usual. Set its full position to `provider: "codex"` or `provider: "claude-code"`, your exact model ID, and a reasoning/effort level supported by the CLI. The wrapper passes the declared output schema without silently rewriting it; use a schema supported by the selected CLI, including `extra='forbid'` on Codex object models. `harness_version` can pin the reported CLI version. `MAVC_CODEX` and `MAVC_CLAUDE` can select executable paths. Both respect `timeout_seconds`; uncertain completion fails and is not retried by these wrappers.

The runtime uses the CLI's normal authentication; MAVC does not read credentials or authenticate for you. A subscription session or API billing follows the CLI's own account configuration. Check that configuration yourself before a live run. The adapters do not claim that a run is free.

## Codex

The caller uses `codex exec --output-schema ... -o ...` in a temporary workspace, read-only sandbox and ephemeral session, with user configuration, rules, hooks, plugins and shell execution disabled. There is one session per call, with no wrapper-level retries. Model and reasoning observations come from the CLI session header; a missing identity or final file fails rather than inventing observations. See the official [noninteractive guide](https://learn.chatgpt.com/docs/non-interactive-mode).

## Claude Code

The caller uses `claude -p --output-format json --json-schema ...` with a custom system prompt, no tools and no persisted session. `--safe-mode` disables customizations while retaining normal CLI authentication. It deliberately does not use `--bare`, whose authentication behavior differs. A successful result must contain `structured_output` and identify one model through `modelUsage` (or a result model field). The recorded reasoning value is the actual `--effort` argument; enforcement by the provider is unobserved. See the official [headless guide](https://code.claude.com/docs/en/headless) and [CLI reference](https://code.claude.com/docs/en/cli-reference).

## Tools and limits

These two wrappers are structured text/model steps. They **do not host MAVC's declared Python tools**, and reject a tool manifest before launching the CLI. Use Pi for the bundled Python-tool bridge. To use tools with Codex or Claude Code instead, expose them through MCP and supply a caller configured for that CLI's MCP support; no MCP server or bridge is built here. These wrappers disable MCP and other tools by default rather than silently pretending tool declarations were executed.

Raw CLI results/header observations are retained with an explicit observation scope. Native CLI tool enumeration, internal CLI/provider retries, transport, compaction, upstream request payloads and provider enforcement are unobserved unless the CLI reports them; a passing settings check does not fill those gaps. There is no new retry machinery or schema-repair loop. Exact CLI/model identifiers are preferable to aliases because a reported model mismatch fails validation.
