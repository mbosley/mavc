# MAVC

MAVC is a plain-Python framework for declared scientific agent flows. Write the work as Python operations, declare the settings that matter, and run it with a record of inputs, outputs, checks and implementation fingerprints. You own the scientific definitions, model adapters and decisions about what counts as an acceptable answer.

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

Save this as a Python file so MAVC can fingerprint its source. Repeating the run in the same folder reuses completed work. Changed inputs, settings or implementations require a fresh run folder.

For an offline **model** operation with a stub adapter:

```sh
python examples/offline.py
mavc describe runs/offline --summary
```

A run folder contains `declaration.json`, `record.json`, content-addressed artifacts under `outputs/objects/`, and a local cache. The record links operation outcomes, input/output artifacts, implementation fingerprints and retained model evidence. `verify(folder)` checks record consistency; it does not establish that an answer is scientifically correct. Records include supplied data and raw model requests and responses; adapters must keep credentials out of returned evidence and error messages.

The core includes sequences, bundles, per-item work, bounded iteration, checks, retries, routing, tools and record inspection. `mavc.learn` adds offline example collection and split/export utilities. `mavc.evals` adds comparisons and calibration; install `.[evals]` for its statistics functions. See [model adapters](docs/model-adapters.md) for the caller contract. No provider client or model account is bundled.

To run the standalone test suite:

```sh
python -m pip install -e '.[test,evals]'
python -m pytest
```

Status: early, version 0.1.0. Interfaces and record formats may change. Training integrations and project-specific flows are outside this release. Licensed under [MIT](LICENSE).
