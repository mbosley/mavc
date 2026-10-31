# Pi agent harness

MAVC's optional Pi adapter targets **public `@earendil-works/pi-coding-agent` 1.4.0**, with **Node >=22.19**. It uses Pi's actual SDK sessions and tool loop. See the public [Pi SDK documentation](https://pi.dev/docs/latest/sdk). The SDK/runtime is installed separately; provider credentials stay with Pi.

## Install and run offline

```sh
python -m pip install -e '.[pi]'
npm install --prefix .pi-runtime --ignore-scripts --no-audit --no-fund @earendil-works/pi-coding-agent@1.4.0
export MAVC_PI_ROOT="$PWD/.pi-runtime"
python examples/pi_agent.py runs/pi-agent
mavc show runs/pi-agent CollectionAgent
```

The Python extra advertises this optional integration; the bridge needs no additional Python dependencies. Pip does not install Node or npm packages. `MAVC_PI_ROOT` points to the directory containing `node_modules`. `MAVC_PI_NODE` can select a Node executable. The npm runtime is ignored by Git. This subprocess bridge is tested on Linux; Windows pipe support is not established.

The example uses Pi's built-in **faux provider**, with scripted assistant turns in `harness_settings.recorded_turns`. Fetch is disabled in this mode. It searches three bundled synthetic texts, reads the relevant document, submits an incorrect quotation, receives a Python rejection, stops with an acknowledgment, receives one correction prompt, and submits the accepted literal quotation. No model credentials, HTTP request or paid inference are involved. The result is a demonstration of execution and checking, not an accuracy evaluation.

## Write an agent step

Subclass `mavc.adapters.pi.Pi` as you would `Operation`. Define Pydantic Input/Output classes and a docstring prompt. Declare `tool_manifest: [tool.describe(), ...]`, put the active tool names in `settings.tools`, and set `request.terminal_tool` to the submission tool's name. `bind_tools(input, request, children)` returns `(tools, context)` for Python's `ToolSession`. The [complete example](../examples/pi_agent.py) defines search, read and submit functions; the document files are explicit fingerprint sources.

Python validates each tool's input and submission. Raising `ToolRejection(ValueError(...))` gives the agent a correction rather than accepting the answer. There is an initial submission plus at most two corrections. If the agent stops after a rejection, the existing bounded continuation supplies a follow-up prompt. A checked submission ends the turn; an acknowledgment alone never completes the operation. Fatal tool exceptions or exhausted submissions fail without a cached answer. The same checks run in recorded and live sessions.

The subclass includes the Python and JavaScript bridge sources in fingerprints. The underlying `call(request, *, tool_dispatch=None, pi_root=None)` also uses the ordinary caller contract; when using it directly on `Operation`, include `sources=Pi.sources`.

## Run live when you choose

Keep the npm runtime installed. Supply `PI_PROVIDER` and `PI_MODEL` as exact public Pi catalog identifiers, optionally `PI_REASONING` (default `off`), and the standard environment credential for that provider, according to [Pi's provider documentation](https://pi.dev/docs/latest/providers). Then:

```sh
python examples/pi_agent.py runs/pi-agent-live --live
```

This opts into real provider calls and can incur charges. The adapter uses in-memory credentials/models/session/settings stores and the public static catalog, with model-network refresh disabled. By default it does not load local auth files, models.json, extensions, skills, AGENTS.md or project context. For a local or custom provider, explicitly set `MAVC_PI_MODELS` to your own Pi models configuration file; only that chosen models file is loaded. Keep credentials out of the file and use Pi's environment references where needed. Include an explicit models file among your operation's `sources` so configuration changes invalidate reuse. No models configuration is bundled. Only declared Python tools are active. The model receives the step's system text and tool/input context; the bridge removes Pi's local working-directory prompt section. A thinking-level clamp or active-tool mismatch fails before the first model call. Configure the level supported by your selected model.

Each invocation owns one subprocess and its timeout/output limit. Pi handles the agent loop; MAVC supplies no new provider retry loop. Default compaction, cache warming and internal retries are disabled. Use `harness_version: "1.4.0"` to retain the target explicitly; the bridge refuses other installed versions until reviewed. Node/Pi must be installed before offline or live execution.

## Settings and evidence

`harness_settings` holds native Pi session settings (for example `compaction`, `transport`, `cacheWarming`, `retry`). `harness_retry.agent_enabled` controls Pi's agent recovery, and `harness_retry.provider_max_retries` controls its provider client's retries. MAVC's outer `retry`/`maximum_attempts` is a separate budget. The defaults avoid nested retries; do not enable two retry owners. `recorded_turns` is a MAVC offline option removed before settings reach Pi. `harness_version` records the runtime version.

The model-call artifact retains assistant messages, normalized `session_requests`, raw `provider_requests` when Pi constructs HTTP payloads, selected settings and runtime version. Python tool calls and continuation events have their own artifacts. Session-request observations are SDK inputs, not proof of provider-side enforcement. The generic settings check marks raw per-attempt HTTP identity and upstream model observations as unobserved when unavailable. Faux sessions construct no HTTP payloads. Keys and authentication headers are not part of these artifacts. As with any adapter, review supplied data and generated errors before sharing a run.

To include the optional public SDK integration tests, set `MAVC_PI_ROOT` and run `python -m pytest`. Tests exercise Pi's real faux tool loop plus a catalog provider with fetch replaced in memory; no live provider test is part of the suite.
