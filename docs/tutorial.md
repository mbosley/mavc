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
with Runner('runs/my-labels', declaration) as runner:
    answer = flow(runner, data)
    runner.finish(answer)
print(answer)
```

`sequence` passes the output of one operation into the next. `for_each` applies one operation to each item, preserving input order; `key` must return a unique, stable ID. Settings use **positions**, built as the parent position + `"."` + the child's operation name. Here the model position is `Review.LabelAll.Classify`. Without the outer sequence, `for_each('LabelAll', Classify(...), ...)` would use `LabelAll.Classify`; a standalone `Classify(...)` uses `Classify`. Setting `positions['Classify']` does not configure its nested instances. Renaming an operation changes its position.

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
