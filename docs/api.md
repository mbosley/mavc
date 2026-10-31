# API guide

MAVC 0.1 is early. Start with the [tutorial](tutorial.md), [record walkthrough](records.md), and [adapter contract](model-adapters.md). Python >=3.11 is required. The package ships annotations and `py.typed`; dynamic input/output schemas mean some boundary types are `Any`, with Pydantic doing runtime validation.

## Glossary

| Term | Meaning |
| --- | --- |
| Operation | A named unit of work, implemented by Python or a model caller. |
| Leaf | An operation that does not invoke children. It can be cached. |
| Composite | An operation declaring composition and invoking children. It assembles results again on resume. |
| Declaration | JSON-compatible configuration: `flow_id`, `positions`, optional `context` and `execution`. |
| Position | An operation's configuration path, such as `Review.LabelAll.Classify`. It describes a reusable place in the flow. |
| Node | An executed instance, such as `Review/0/one`. Several item nodes share one position. |
| Kind | A node's operation category; normally its operation name. Low-level `Runner.step` settings use `declaration.nodes[kind]`. |
| Children | The context passed to `run`: effective settings, child invocation, details and evidence. |
| Artifact | A retained typed JSON value referenced by content hash. |
| Fingerprint | A hash of defining source files and explicitly supplied helper sources, used to detect code changes. |
| Run record | Outcomes, artifacts, settings, fingerprints and dependencies for one run directory. |

## Operations and composition

Import `Operation`, `sequence`, `bundle`, `for_each` and `iterate` from `mavc`.

| API | Contract |
| --- | --- |
| `Operation(name=None, *, run=None, composition=None, sources=(), structure=None, caller=None)` | Default name is the class name. Define `Input`/`Output` as Pydantic model classes, or leave them unset for JSON data. Override `run(children, input)` for Python; inherited `run` invokes `caller(request)`. `sources` names extra helper callables/files to fingerprint. |
| `operation(runner, input, *, node=None, kind=None, depends_on=())` | Validates input, executes/reuses work and validates output. Optional `name` also supplies the root node ID. Normal callers do not set internal parent/position arguments. |
| `sequence(name, operations)` | Pass each result to the next operation in order. |
| `bundle(name, {key: operation, ...})` | Pass the same input to every operation; return a keyed dictionary. Executes sequentially. |
| `for_each(name, operation, *, key)` | Apply to each input item, preserving input order. Keys must be unique stable IDs. Configure `max_concurrency` at this composite's position. |
| `iterate(name, operation, *, bound, stop)` | Apply repeatedly, up to a positive integer bound. `stop(value)` sees the child's typed result when it has an Output model. |
| `mavc.operation.route(name, local, frontier, *, valid=...)` | Local child returns a Prediction-shaped value. Use its answer if valid and at/above the position's `cutoff`; otherwise call frontier once. Return a routed-result dictionary. |

**Returns:** An operation with `Output` returns an instance of that Pydantic model, including on cache reuse. An untyped operation returns its Python JSON-compatible value. Built-in `sequence` returns the final child's JSON value; `bundle` returns a dictionary; `for_each` returns a list; `iterate` returns the final child's JSON value; the operation-level `route` returns a dictionary. Child models are converted with `model_dump(mode='json')` at built-in composite boundaries. No return-type unification is implicit in `Runner.finish`: pass a JSON value, converting typed leaf results yourself.

For custom composition, declare `composition='custom'` and call `children(instance_name, operation, input, *, node=None, kind=None, depends_on=(), order=None)`. Instance names build node IDs with `/`; operation names build positions with `.`. `children.settings` contains effective settings; `children.details` is mutable recorded detail; `children.evidence(artifact.ref())` links a retained artifact. Define scientific policy in the flow, not in these bookkeeping fields.

## Declarations and settings

`Declaration.model_validate(mapping)` or `Declaration.load(path)` builds configuration; `snapshot()` returns the retained effective declaration. `for_position(position)` returns its effective configuration. `Settings` rejects unknown fields. Model leaves need `provider`, `model` and `reasoning` at their **full dotted position**; these strings are adapter-owned identifiers. A bare `Classify` entry does not configure `Review.LabelAll.Classify`. Per-item nodes all share that position's settings.

Useful settings are `timeout_seconds`, `max_concurrency`, `maximum_attempts`, `retry` (`max_attempts`, `initial_interval`), `cutoff`, `prompt_budget_chars` and `prompt_budget_reason`. The adapter must actually implement requested provider controls; MAVC checks supplied observations and records missing ones. `context` holds caller-owned declaration data; there is no flow compiler or provider registry.

Model leaves render their docstring and input/output schemas into a request. Override `render` and/or `parse` if needed. Generic Python callables must return JSON-compatible values; model callers return the documented observation envelope. See [model-adapters.md](model-adapters.md), including tool binding and compatibility fields.

## Runs, failures and corrections

`Runner(folder, declaration, *, flow=None, sources=(), metadata=None, reuse_from=None, rerun_from=None, protect_reused=False)` is a context manager. `flow=flow` validates known model positions before any run folder is created; `flow.validate(declaration)` is also available. Built-in composites keep references to their children for this check; arbitrary dynamic Python compositions must explicitly validate their known children. Call operations inside it and then `runner.finish(json_output, *, outcome=None)`. Closing saves the record and closes the cache. Completion is explicit; returning from the context alone does not mark the run complete.

- `runner.step(node, input, operation, *, kind=None, sources=(), depends_on=(), retry=False, output_type='node-result/v1')` is the low-level API; `operation()` receives no arguments. Prefer `Operation` for typed/composed flows.
- `runner.retain(Artifact.create(type, value, lineage=()))` stores and returns an artifact. `runner.index()` maps executed IDs to typed NodeRecords. `runner.fanout(items, operation, *, max_concurrency=None)` returns results in input order.
- `Rejected`, `NeedsCorrection` and `NeedsHumanReview` record distinct scientific outcomes. They are exported from `mavc` and are not automatically retried.
- For a transient, known safe failure in a Python step, propagate `RetryableFailure`, set `Operation.retry = True`, and declare a bounded retry budget at that position. The default model boundary retains adapter exceptions and surfaces `RuntimeError`; model retry safety must be owned explicitly by a custom Python step. Generic errors and uncertain completion fail without automatic retries. `mavc.runner.ToolFailure` is a separate opt-in technical recovery path requiring `retry_on='tool_failure'` and exactly two attempts. Attempt budgets persist on resume.
- An unchanged directory reuses completed leaves. `mavc.runner.NewRunRequired` means inputs, configuration, code or child inventory changed; its message names the affected node or declaration path. Choose a fresh folder, preserving the old one.
- Selective correction uses a fresh destination, `reuse_from='prior-run'` and `rerun_from='node-id'` (or a kind). It reruns that node, descendants and dependent downstream nodes. Ancestors rebuild their results. Source fingerprints cover files, so one file edit can invalidate multiple nodes. `protect_reused=True` refuses to invoke formerly completed reused nodes on a cache miss. `RetainedFailure` means failed work needs an explicit correction run.

## Results, evidence and offline utilities

`mavc run <script> [args...]` executes a script with concise errors; set `MAVC_DEBUG=1` for the full traceback. `show` and `describe` share this error policy. Direct Python execution keeps normal tracebacks. `mavc describe <run> --summary` gives a readable outline. `mavc show <run> [node-id]` returns JSON with resolved results or a node's input/output/evidence/errors; it omits fingerprint tables. Both commands are read-only. `describe --details` includes the raw record.

Python APIs in `mavc.record`: `show(folder, node=None) -> dict`, `read_json(path)`, `resolve(folder, ref) -> Artifact` (read `.value`), and `verify(folder) -> dict`. `Artifact.ref()` returns a reference. `digest(value)` hashes a JSON value. `walk_nodes(record, leaves_only=False)` from `mavc` yields `(node_id, node)` pairs for JSON or typed records. Consistency checks never establish scientific correctness. See [records.md](records.md).

The independent `mavc.route.route(action_id, input, *, local, frontier, cutoff, local_model, frontier_model, valid=...)` accepts callbacks and returns a `Routed` dataclass; `Prediction` holds the local answer/confidence/abstention, and `RouteFailure.result` retains failed frontier routing.

`mavc.learn` offers `Candidate`, `Example`, `Decision`, `Admission`, `collect`, `admit`, `split`, `check_splits` and `export` for caller-defined training examples. `mavc.evals` offers comparison matrices, completeness and stability, plus probability metrics and calibration with `.[evals]`. These utilities do not supply scientific labels, thresholds or training implementations. `mavc.tools` exposes typed `Tool`, `ToolInput`, `ToolSession` and `ToolRejection` for adapters that bind tools; quote checks remain application-owned.
