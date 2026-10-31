# A model step followed by a code check

Start with the README's installation. Run the complete example with:

```sh
python examples/tutorial.py
mavc describe runs/tutorial --summary
mavc show runs/tutorial
mavc show runs/tutorial Review/0/one
```

It labels three texts, then returns `{'total': 3, 'counts': {'question': 2, 'statement': 1}}`. It makes no network calls. `mavc.stub.classify` is a bundled deterministic adapter: a trailing question mark means “question”. Its purpose is to teach the flow contract, not to judge language or demonstrate scientific accuracy.

## Define the two steps

The executable source is [examples/tutorial.py](../examples/tutorial.py). The model operation has Pydantic input/output types and a docstring that becomes its system prompt:

```python
from typing import Literal
from pydantic import BaseModel, RootModel
from mavc import Operation, Rejected, Runner, for_each, sequence
from mavc.stub import classify

class Label(BaseModel):
    id: str
    label: Literal['question', 'statement']

class Classify(Operation):
    """Label the supplied text as a question or statement and preserve its ID."""
    class Input(BaseModel):
        id: str
        text: str
    Output = Label

class Check(Operation):
    """Check unique nonempty IDs and count the accepted labels."""
    Input = RootModel[list[Label]]
    class Output(BaseModel):
        total: int
        counts: dict[str, int]

    def run(self, children, input):
        rows = input.root
        ids = [row.id for row in rows]
        if any(not identifier.strip() for identifier in ids) or len(set(ids)) != len(ids):
            raise Rejected('Each result needs a unique nonempty ID')
        return {'total': len(rows), 'counts': {
            label: sum(row.label == label for row in rows)
            for label in ('question', 'statement')}}
```

Overriding `run` makes `Check` a Python step; leaving it inherited makes `Classify` a model step using its supplied `caller`. The `children` argument is a context for settings, child calls and evidence. This code check does not need it. Pydantic validates labels; `Rejected` expresses the flow's additional acceptance rule. A docstring saying “check” has no validation effect on its own.

## Compose and configure

```python
flow = sequence('Review', [
    for_each('LabelAll', Classify(caller=classify), key=lambda row: row['id']),
    Check(),
])
declaration = {'flow_id': 'text-labels/v1', 'positions': {
    'Review.LabelAll.Classify': {
        'provider': 'stub', 'model': 'punctuation-v1', 'reasoning': 'none',
    },
}}
data = [{'id': 'one', 'text': 'Is the record complete?'},
        {'id': 'two', 'text': 'The input is supplied.'},
        {'id': 'three', 'text': 'Can we inspect the evidence?'}]
with Runner('runs/my-labels', declaration, flow=flow) as runner:
    answer = flow(runner, data)
    runner.finish(answer)
print(answer)
```

`sequence` passes the output of one operation into the next. `for_each` applies one operation to each item, preserving input order; `key` must return a unique, stable ID. Settings use **positions**, built as the parent position + `"."` + the child's operation name. Here the model position is `Review.LabelAll.Classify`. Without the outer sequence, `for_each('LabelAll', Classify(...), ...)` would use `LabelAll.Classify`; a standalone `Classify(...)` uses `Classify`. Setting `positions['Classify']` does not configure its nested instances. Renaming an operation changes its position. `Runner(..., flow=flow)` validates known model positions before creating the folder and suggests the nearest declared spelling when one is missing. Calling a built-in flow also validates its known children before any step executes. Custom Python compositions whose children are created dynamically can validate them explicitly with `child.validate(declaration, position=full_path)`; MAVC cannot discover an arbitrary Python call graph.

Node IDs identify actual invocations: `Review/0/one` is the model call for the first text, while its position remains `Review.LabelAll.Classify`. The `0` comes from the first operation in the sequence; `one` comes from `key`. All three item nodes share the same position settings. The check is `Review/1`, with position `Review.Check`. See `mavc show runs/tutorial` for the executed IDs.

The `provider`, `model` and `reasoning` strings are adapter identifiers; the stub uses these only to report observations. For a real model, replace the caller with your own adapter following [the adapter contract](model-adapters.md). Scientific prompts and acceptance rules remain yours.

## Returns, retries and changed runs

A typed leaf returns its `Output` Pydantic model. Compositions made by `sequence`, `bundle` and `for_each` convert child outputs to JSON-compatible values, so this flow returns a dictionary. Pass that dictionary directly to `runner.finish`; for a typed leaf use `runner.finish(answer.model_dump(mode='json'))`. The [API guide](api.md) gives the complete return contracts.

An unchanged run directory reuses completed leaves. Retry is opt-in for Python steps that propagate `RetryableFailure`: set the step's `retry = True` and, at its full position, add `retry: {'max_attempts': 2, 'initial_interval': 1}`. Raise that exception only for a known safe transient failure. The default model boundary retains adapter exceptions and surfaces `RuntimeError`; it does not infer safe retry from a provider error. If you need model retries, implement a Python operation that explicitly owns that boundary and raises `RetryableFailure` only when it knows no call completed. Generic errors, rejected science and unknown completion do not get retried. Disable nested adapter retries or declare them; MAVC prevents two retry owners from multiplying the call budget. This stub does not need retries.

When you edit data, a prompt/docstring, code or settings, select a fresh directory, for example `python examples/tutorial.py runs/tutorial-v2`. A refusal names the changed node or declaration path. It preserves the prior run; do not edit records or delete their cache to force acceptance. A full new run is the easiest first choice. To correct a known step while reusing unaffected work:

```python
with Runner('runs/correction', declaration,
            reuse_from='runs/my-labels', rerun_from='Review/1') as runner:
    answer = flow(runner, data)
    runner.finish(answer)
```

`rerun_from` selects that node, its children and dependent downstream work. Ancestor compositions execute again to assemble results. This example selects the final check; select an item node to rerun a particular model call. Changing a shared source file can invalidate several nodes because fingerprints cover defining files, so use a full fresh run if broader changes make reuse refuse. The stub's result is a demonstration, not an evaluation on held-out documents.

## Concise errors

Use `mavc run examples/tutorial.py runs/tutorial-v3` to run a script with one-line errors and a nonzero exit code on failure. It forwards arguments after the filename. `MAVC_DEBUG=1 mavc run ...` restores Python tracebacks. `show` and `describe` use the same error handling, including missing run paths. Direct `python script.py` keeps normal Python exception behavior.

## Use a provider when you choose to

[examples/anthropic_adapter.py](../examples/anthropic_adapter.py) is a worked, opt-in adapter using Python's standard library. It follows the official [Messages API](https://platform.claude.com/docs/en/api/messages/create) and [authentication contract](https://platform.claude.com/docs/en/api/overview). Importing the file does not call the service. Its HTTP boundary is mocked in tests; no live-provider result is claimed.

To adapt the flow above, first supply your own `ANTHROPIC_API_KEY` and `ANTHROPIC_MODEL` through your environment, outside the source and run record. Use a workspace-scoped key and an exact model ID supporting effort and JSON-schema output. Do not put either key or account details in a declaration. Then replace the caller and settings and choose a new run folder:

```python
import os
from examples.anthropic_adapter import call

flow = sequence('Review', [
    for_each('LabelAll', Classify(caller=call), key=lambda row: row['id']), Check(),
])
declaration = {'flow_id': 'text-labels/provider-v1', 'positions': {
    'Review.LabelAll.Classify': {
        'provider': 'anthropic', 'model': os.environ['ANTHROPIC_MODEL'], 'reasoning': 'low',
        'settings': {'transport': 'json', 'max_output_tokens': 512},
    },
}}
# Running this block makes real API calls using your own account.
with Runner('runs/provider-labels', declaration, flow=flow) as runner:
    runner.finish(flow(runner, data))
```

Run this from a script in the repository root so `examples` can be imported. The example supports flat object schemas such as `Label`, one non-streaming HTTP request, a timeout, no tools and no automatic retries. It maps declared reasoning to provider effort and keeps raw JSON request/response bodies without authentication headers. `observed.request` is a normalized settings view; `provider_request` is the exact wire JSON. Missing optional provider observations remain explicit. Truncation, refusal, model-ID mismatch and invalid output fail rather than becoming accepted cached results. A live call can incur provider charges; the bundled stub remains the default for every offline example and test.

## A DataFrame of per-item results

Pandas stays outside MAVC's core dependencies:

```sh
python -m pip install -e '.[examples]'
python examples/dataframe.py runs/tutorial
```

The [example](../examples/dataframe.py) reads the recorded tree, resolves completed outputs at `Review.LabelAll.Classify`, and returns a DataFrame with `node`, `id`, `label` columns. For the tutorial it has three rows. In a notebook or repository-root script:

```python
from examples.dataframe import labels_frame
labels = labels_frame('runs/tutorial')
print(labels.groupby('label').size())
```

This table includes only completed model-item results at that position; failed or missing items are not rows. Check `mavc show runs/tutorial` for all node outcomes before interpreting table counts. It reads records without changing them.

## An agent with tools and a checked correction

The earlier `Classify` example is one model call per item. [examples/pi_agent.py](../examples/pi_agent.py) demonstrates an agent session within one step: declare search/read/submit tools, supply their Python implementations through `bind_tools`, and validate the submission in Python. Its default recorded session uses public Pi 1.4.0's actual faux agent loop without network or credentials. It first submits a bad quote, gets rejected, receives a bounded correction prompt and submits the literal source text. The files in [examples/collection](../examples/collection) are synthetic teaching material and explicit fingerprint sources.

Install the runtime with [docs/pi.md](pi.md), then run:

```sh
python examples/pi_agent.py runs/collection-agent
mavc show runs/collection-agent CollectionAgent
```

Inspect the rejected and accepted `model-tool-call/v1` artifacts and the `model-session-event/v1` correction. Use a fresh folder for `--live`, with your own provider/model environment settings. The same Python checker runs live; no checked answer means no accepted output. This demonstrates execution semantics, not model accuracy.

If you already use Codex or Claude Code, their [small caller adapters](cli-harnesses.md) support schema-constrained model steps behind the same interface. They do not directly host these Python tools; MCP is the route for a separate tool-capable caller. Other `Operation(caller=...)` adapters remain supported.
