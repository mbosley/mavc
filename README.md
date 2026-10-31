# MAVC

MAVC is a small Python framework for model pipelines you can check, rerun and change one assumption at a time, built so both researchers and coding agents can work with it.

MAVC helps people, including beginners, put together AI pipelines in plain Python. You can connect your own model API keys or use your signed-in Claude Code or Codex subscription through a caller adapter.

**How it is meant to be used.** You decide the science and acceptance rules, inspect the records and decide what to publish. Your coding agent writes and runs the flow, with Python simple enough to write by hand.

There are three kinds of steps:

- **Code:** a deterministic Python function, such as cleaning text or checking an answer. Use it when the rule can be written directly.
- **Model call:** send one bounded task to a model and validate its response. Use it when the input and expected output are clear.
- **Agent:** give a harness a goal and tools, and let it choose its next actions in a full loop. Use it when the work needs search, reading or correction along the way. Agents can make several model and tool calls, so they are heavier. Pi hosts Python tools directly; the current Codex and Claude Code wrappers are text steps, with MCP as the route for a separate tool-capable caller.

Other people with the same code and models can rerun your declared flow. The flow definition, inputs and declared settings are reproduced exactly, and every call is recorded. Model outputs can still vary between runs; the record keeps what came back so you can inspect it, repeat the run and compare results. You own the scientific definitions and decisions about which answers to accept.

Requires Python 3.11 or newer. From a copy of this repository:

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e .
```

Steps use their class name (or an explicit operation name). Settings belong to **positions**: a parent and child form `LabelAll.Classify`; nesting under `Review` makes `Review.LabelAll.Classify`. Executed **nodes** use slashes instead, such as `Review/0/one`, to identify a particular item. A model step's class docstring serves as its prompt. The bundled [`mavc.stub.classify`](src/mavc/stub.py) lets you try this offline without writing an adapter; see the [tutorial](docs/tutorial.md).

Here is a small flow that runs offline:

```python
from pydantic import BaseModel
from mavc import Operation, Runner, sequence, verify

class Increment(Operation):
    """Add one to the supplied number."""
    class Input(BaseModel):
        value: int
    Output = Input

    def run(self, children, input):
        return {"value": input.value + 1}

flow = sequence("TwoSteps", [Increment(), Increment()])
declaration = {"flow_id": "increment/v1"}
with Runner("runs/increment", declaration) as runner:
    answer = flow(runner, {"value": 1})
    runner.finish(answer)

print(answer)  # {"value": 3}
print(verify("runs/increment")["completed"])  # True
```

Save this as a Python file so MAVC can fingerprint its source (record which code ran). REPL and notebook-only definitions have no source file: put operations and helpers in an importable `.py` module and call them from your notebook. Repeating the run in the same folder reuses completed work. Use a fresh folder to repeat model calls instead of reusing cached answers. Changed inputs, settings or implementations require a fresh run folder.

For an offline **model** operation with a stub adapter:

```sh
python examples/offline.py
mavc describe runs/offline --summary
```

Next, follow the [model → code-check tutorial](docs/tutorial.md), which uses a bundled offline classifier, per-item work and a final code check. The [API guide and glossary](docs/api.md) cover composition, settings paths, return types and retries. Coding agents should start with [AGENTS.md](AGENTS.md).

Read final results or a node's inputs, outputs and model evidence without opening hash-named files:

```sh
mavc show runs/offline
mavc show runs/offline CountWords
```

For concise errors when running your own script, use `mavc run my_flow.py` (arguments follow the filename). `MAVC_DEBUG=1 mavc run my_flow.py` restores the full traceback. Passing `flow=flow` to `Runner` checks model positions before creating a run folder; built-in flows also check before their first step.

See the [record walkthrough](docs/records.md) for Python access and failed runs.

A run folder contains `declaration.json`, `record.json`, content-addressed artifacts under `outputs/objects/`, and a local cache. The record links operation outcomes, input/output artifacts, implementation fingerprints and retained model evidence. `verify(folder)` checks record consistency; it does not establish that an answer is scientifically correct. Records include supplied data and raw model requests and responses; adapters must keep credentials out of returned evidence and error messages.

The core includes sequences, bundles, per-item work, bounded iteration, checks, retries, routing, tools and record inspection. `mavc.learn` adds offline example collection and split/export utilities. `mavc.evals` adds comparisons and calibration; install `.[evals]` for its statistics functions. See [model adapters](docs/model-adapters.md) for the caller contract. Core flows accept any caller. Optional harness integrations invoke your own installed runtimes; no account configuration is bundled. The [Anthropic HTTP example](examples/anthropic_adapter.py) remains available for a direct API adapter.

| Harness | Python interface / extra | Runtime checked | Declared Python tools |
| --- | --- | --- | --- |
| Pi | `mavc.adapters.pi.Pi`, `.[pi]` | Public Pi 1.4.0; Node >=22.19 | Yes; checked submission and correction |
| Codex | `mavc.adapters.codex.Codex`, `.[codex]` | CLI 0.164.0; subprocesses mocked | No; use a custom MCP caller |
| Claude Code | `mavc.adapters.claude_code.ClaudeCode`, `.[claude-code]` | CLI 2.1.310; subprocesses mocked | No; use a custom MCP caller |
| Other adapters | `Operation(caller=...)` | Caller-owned | Caller-owned |

The offline agent example **replays a scripted session** to show declared tools, a deliberately rejected submission and its correction. No real model chooses the tools in that mode. Pi is an external agent runtime installed from npm; Node runs its tool loop while Python owns the checks. Follow [Pi setup](docs/pi.md), then run `python examples/pi_agent.py`. In `--live` mode, a real model chooses which tools to call and what to submit. See [CLI harnesses](docs/cli-harnesses.md) for Codex and Claude Code. The extras add no Python dependencies; install the selected runtime separately.

For a results table, install `.[examples]` and run `python examples/dataframe.py runs/tutorial` after the tutorial. [The small example](examples/dataframe.py) turns completed item labels into a pandas DataFrame; pandas is optional.

To run the standalone test suite:

```sh
python -m pip install -e '.[test,evals]'
python -m pytest
```

## Related tools

[DSPy](https://dspy.ai/) also lets you declare language-model programs, and focuses on optimising prompts and modules against a metric. Workflow and tracking tools such as [MLflow](https://mlflow.org/docs/latest/ml/tracking/) add run recording and comparison alongside a pipeline. MAVC combines established components around a research contract: code, inputs and declared settings define a run, a change requires a new run, and local records retain every declared call, tool call and check, including Python tools inside agent harnesses. It is built for pipelines that need to be rerun, varied and audited, and deliberately does not optimise prompts.

Status: early, version 0.1.0. Interfaces and record formats may change. Training integrations and project-specific flows are outside this release. Licensed under [MIT](LICENSE).
