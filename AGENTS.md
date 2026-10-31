# Working with MAVC

You are the person's coding partner: write and run clear Python flows, apply their checks, and present records they can inspect. Leave scientific definitions, acceptance rules and decisions about publishing results to the person. The code should stay simple enough to write by hand.

MAVC helps people, including beginners, build AI pipelines using their API keys or signed-in Codex/Claude Code subscriptions through adapters. Choose the simplest step that fits:

- **Code:** deterministic Python when the rule can be written directly.
- **Model call:** one bounded task with clear input/output types.
- **Agent:** a harness choosing tools and looping through search, reading or correction. This is heavier because it can make several calls. Pi supports Python tools directly; Codex/Claude Code need a separate tool-capable MCP caller with the current wrappers.

The flow, inputs and declared settings are reproducible exactly with the same implementation and models. Model outputs may vary: retain what each call returned, repeat changed work in fresh folders and compare records.

Read the [API/glossary](docs/api.md), [tutorial](docs/tutorial.md) and [record guide](docs/records.md). The working contract is:

- Subclass `Operation` with Pydantic `Input`/`Output`. For code, implement `run(self, children, input)`; for a model, inherit `run` and provide `caller=adapter`. The model class docstring is its prompt. Define operations in source `.py` files; notebooks can import them.
- Settings paths use parent + `"."` + operation name. `for_each('LabelAll', Classify(...), key=...)` uses `LabelAll.Classify`; inside `sequence('Review', ...)` it uses `Review.LabelAll.Classify`. Executed node IDs use slashes, such as `Review/0/one`.
- Use `with Runner(folder, declaration, flow=flow) as runner:`, call the flow, then `runner.finish(answer)`. A typed leaf returns a Pydantic model: finish with `answer.model_dump(mode='json')`. Built-in composites return JSON data. Model settings validate before folder creation.
- Optional adapters: [Pi](docs/pi.md), [Codex and Claude Code](docs/cli-harnesses.md). Install their external runtimes separately. `examples/pi_agent.py` defaults to scripted replay; no real model chooses tools there. `--live` uses the configured model and may incur charges.
- `mavc run script.py` gives concise errors; `MAVC_DEBUG=1` restores traces. Read structure with `mavc describe <run> --summary`, results/IDs with `mavc show <run>`, and values/evidence with `mavc show <run> <node>`.
- Never edit records, artifacts or caches. Reuse only unchanged work; new inputs/settings/prompts/code need a new folder. Selective correction uses `reuse_from` and `rerun_from`.
- Run `python -m pytest` with `.[test,evals]`; Pi integration tests also need its documented runtime and `MAVC_PI_ROOT`. Run offline examples in fresh folders. No paid call is needed for checks. Consistent records and successful transport never establish scientific correctness.
