# Read a run

After `python examples/tutorial.py`, start with the human-readable outline:

```sh
mavc describe runs/tutorial --summary
```

It shows composition, configured models, recorded numerical results and nested dictionaries, plus failed-step counts. List results and executed node IDs as JSON:

```sh
mavc show runs/tutorial
```

This returns the run `outcome`, final `output` (including the nested label counts), and `nodes`, a mapping from executed IDs to their position and outcome. It works for failed runs too: final output is `null` when none was accepted. Read a specific call:

```sh
mavc show runs/tutorial Review/0/one
```

The node view resolves its `input`, `output`, `evidence`, `errors`, attempts and reuse flag. For that item, input contains its ID and source text, output contains its label, and `evidence` includes a `model-call/v1` value. That value contains the rendered request, returned response, prompt size and `settings_check`. The check has `checked`, `unobserved` and `mismatches` lists; an absent observation does not prove a setting was followed. If a call or output check fails, inspect its retained response and error here. The final Python check is at `Review/1`.

The same access is available without a CLI:

```python
from mavc.record import show, read_json, resolve

run = show('runs/tutorial')
print(run['output']['counts'])
call = show('runs/tutorial', 'Review/0/one')
print(call['input'], call['output'])
print(call['evidence'][0]['value']['settings_check'])

# Low-level access when processing record files yourself:
record = read_json('runs/tutorial/record.json')
print(resolve('runs/tutorial', record['outputs']).value)
```

`declaration.json` is the effective run configuration. `record.json` holds the invocation tree, outcomes, dependency links, settings and source fingerprints. An artifact is a typed JSON value with a content hash and lineage; the hash lets several references point to the same value in `outputs/objects/`. The cache supports reuse. `show` follows artifact references for you and leaves out the repeated implementation tables. `describe --details` still exposes the full raw record when needed. Stored fingerprints remain repeated to keep the existing record format unchanged.

`verify(folder)` checks the record and artifacts for consistency and reports completion. It does not judge labels or establish scientific validity. Both `show` views expose supplied data and raw model evidence, so review those before sharing a run. Never edit records or artifacts; use a new run folder for changed work.
