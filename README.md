# MAVC

MAVC is a small Python framework for model pipelines you can check, rerun and change one assumption at a time, built so both researchers and coding agents can work with it.

When you use language models in research, you need to know exactly what ran, check what came out, rerun it, and change one assumption to see what moves. MAVC lets you write your pipeline as plain Python steps, model steps and code checks alike, and keeps a record of every call. Reruns reuse finished work, and a changed setting gives you a new run you can compare against the old one. It was built for, and used in, research pipelines that read whole literatures and document collections.

Write the work as Python operations, declare the settings that matter, and run it with a record of inputs, outputs, checks and implementation fingerprints. You own the scientific definitions, model adapters and decisions about what counts as an acceptable answer.

Requires Python 3.11 or newer. From a copy of this repository:

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e .
```

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

Save this as a Python file so MAVC can fingerprint its source (record which code ran). REPL and notebook-only definitions have no source file: put operations and helpers in an importable `.py` module and call them from your notebook. Repeating the run in the same folder reuses completed work. Changed inputs, settings or implementations require a fresh run folder.

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

See the [record walkthrough](docs/records.md) for Python access and failed runs.

A run folder contains `declaration.json`, `record.json`, content-addressed artifacts under `outputs/objects/`, and a local cache. The record links operation outcomes, input/output artifacts, implementation fingerprints and retained model evidence. `verify(folder)` checks record consistency; it does not establish that an answer is scientifically correct. Records include supplied data and raw model requests and responses; adapters must keep credentials out of returned evidence and error messages.

The core includes sequences, bundles, per-item work, bounded iteration, checks, retries, routing, tools and record inspection. `mavc.learn` adds offline example collection and split/export utilities. `mavc.evals` adds comparisons and calibration; install `.[evals]` for its statistics functions. See [model adapters](docs/model-adapters.md) for the caller contract. No provider client or model account is bundled.

To run the standalone test suite:

```sh
python -m pip install -e '.[test,evals]'
python -m pytest
```

Status: early, version 0.1.0. Interfaces and record formats may change. Training integrations and project-specific flows are outside this release. Licensed under [MIT](LICENSE).
