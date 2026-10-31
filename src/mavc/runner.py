"""Explicit Python steps, content cache, bounded retries and downstream reruns."""
from __future__ import annotations
from typing import Any, Callable, Iterable, TYPE_CHECKING
if TYPE_CHECKING:
    from .operation import Children, Operation
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import RLock
import shutil
import importlib.metadata

from diskcache import Cache
from tenacity import Retrying, retry_if_exception_type, stop_after_attempt, wait_fixed

from .declare import Declaration, call_attempts, source_identity
from .fingerprint import fingerprint
from .record import Artifact, NodeRecord, Outcome, RunRecord, complete_children, digest, now, read_json, resolve, walk_nodes, write_json


class RetryableFailure(RuntimeError):
    """Caller proves a capacity/pre-call failure; unknown completion is not eligible."""


class ToolFailure(RuntimeError):
    """A declared scientific caller proved a technical tool failure, not abstention."""


class Rejected(ValueError):
    pass


class NeedsCorrection(ValueError):
    pass


class NeedsHumanReview(ValueError):
    pass


class NewRunRequired(ValueError):
    pass


class ProtectedReuseMiss(NewRunRequired):
    """A reused completed node no longer matches its cached identity; calling it again would replace an accepted result."""


class RetainedFailure(RuntimeError):
    def __init__(self, node, outcome):
        self.outcome = outcome
        super().__init__(f'{node}: {outcome.reason}; use explicit rerun-from')


def failure_outcome(error: Exception) -> Outcome:
    if isinstance(error, RetainedFailure):
        return error.outcome
    status = {Rejected: 'rejected', NeedsCorrection: 'needs_correction',
              NeedsHumanReview: 'needs_human_review'}
    return Outcome(status=next((v for k, v in status.items() if isinstance(error, k)), 'failed'), reason=str(error))


class Runner:
    def __init__(self, folder: str | Path, declaration: Declaration | dict, *, implementations: dict | None = None, sources: tuple = (), metadata: dict | None = None, rerun_from: str | None = None, reuse_from: str | Path | None = None,
                 protect_reused: bool = False, flow: Operation | None = None) -> None:
        self.declaration = Declaration.model_validate(declaration)
        if flow is not None:
            flow.validate(self.declaration)
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)
        self.implementations = {} if implementations is None else implementations
        self.sources = tuple(sources)
        self.core = {'mavc/' + name: value for name, value in
            source_identity(Path(__file__).parent.glob('*.py'), Path(__file__).parent).items()}
        self.lock = RLock()
        self.record_path = self.folder / 'record.json'
        snapshot = self.declaration.snapshot()
        if self.record_path.exists():
            if reuse_from or rerun_from:
                raise ValueError('A correction needs a fresh destination run directory')
            self.record = RunRecord.model_validate(read_json(self.record_path))
            if self.record.declaration_sha256 != digest(snapshot):
                previous = read_json(self.folder / 'declaration.json')
                changed = [f'positions.{key}' for key in sorted(set(previous.get('positions', {})) | set(snapshot.get('positions', {})))
                           if previous.get('positions', {}).get(key) != snapshot.get('positions', {}).get(key)]
                changed += [key for key in sorted(set(previous) | set(snapshot)) if key != 'positions' and previous.get(key) != snapshot.get(key)]
                raise NewRunRequired(f'Changed declaration: {", ".join(changed)}; use a new run directory. '
                                     'Keep the prior directory to inspect or selectively reuse unchanged work')
        else:
            self.record = RunRecord(declaration_sha256=digest(snapshot), metadata={**(metadata or {}), 'dependencies': {name: importlib.metadata.version(name)
                for name in ['pydantic', 'diskcache', 'tenacity']}})
        if rerun_from and not reuse_from:
            raise ValueError('rerun-from requires a prior run and a fresh destination')
        if self.record_path.exists():
            for _, node in walk_nodes(self.record):
                current = self.implementations.get(node.kind)
                if current is not None:
                    expected = self.implementation_for(node.kind)
                    known = {k: v for k, v in node.implementation.items()
                             if k in expected or not k.startswith('caller/source/')}
                    if known != expected:
                        raise NewRunRequired(f'Node {node.node!r}: implementation changed; use a new run directory')
        self.original = self.record.model_copy(deep=True)
        self.cache = Cache(str(self.folder / 'cache'))
        self.protected = set()  # with protect_reused: prior completed nodes that must come from the cache, never a new call
        if reuse_from:
            source = Path(reuse_from)
            prior = RunRecord.model_validate(read_json(source / 'record.json'))
            selected = self.descendants(prior, rerun_from) if rerun_from else set()
            shutil.copytree(source / 'outputs/objects', self.folder / 'outputs/objects', dirs_exist_ok=True)
            for name, node in walk_nodes(prior):
                if name not in selected and not node.composition and node.outcome.status == 'completed':
                    self.cache[(name, node.input_sha256, node.declaration_sha256)] = {'output': node.output, 'details': node.details}
                    if protect_reused:
                        self.protected.add(name)
            self.record.metadata['previous_run'] = str(source.resolve())
            self.record.metadata['rerun_nodes'] = sorted(selected)
        write_json(self.folder / 'declaration.json', snapshot)
        self.invocation = {'began_at': now(), 'rerun_from': rerun_from}
        self.record = self.record.model_copy(update={'invocations': [*self.record.invocations, self.invocation]})
        self.save()

    def implementation_for(self, kind: str, operation: Callable | None = None, sources: tuple = ()) -> dict:
        sources = self.sources + tuple(sources)
        caller = self.implementations.get(kind)
        if caller is None:
            caller = fingerprint(operation, sources)
        elif sources:
            caller = {**caller, **fingerprint(None, sources)}
        return {**self.core, **{'caller/' + name: value for name, value in caller.items()}}

    def save(self) -> None:
        write_json(self.record_path, self.record.model_dump())

    def index(self) -> dict[str, NodeRecord]:
        return dict(walk_nodes(self.record))

    def retain(self, artifact: Artifact) -> Artifact:
        write_json(self.folder / 'outputs/objects' / (artifact.sha256 + '.json'), artifact.model_dump())
        return artifact

    @staticmethod
    def descendants(record: RunRecord, node: str) -> set[str]:
        index = dict(walk_nodes(record))
        selected = {name for name, rec in index.items() if name == node or rec.kind == node}
        if not selected:
            raise ValueError(f'Unknown rerun node: {node}')
        parents = {child: name for name, rec in index.items() for child in rec.children}
        while True:
            affected = set(selected)
            for name in selected:
                while name in parents:
                    name = parents[name]
                    affected.add(name)
            downstream = {name for name, rec in index.items() if set(rec.depends_on) & affected}
            downstream |= {child for name in selected for child in index[name].children}
            if downstream <= selected:
                return selected
            selected |= downstream

    def step(self, node: str, input: Any, operation: Callable[[], Any], *, kind: str | None = None, sources: tuple = (), depends_on: Iterable[str] = (), retry: bool = False,
             output_type: str = 'node-result/v1', context: Children | None = None) -> Any:
        """Operation returns JSON only. Call and scientific check are separate steps."""
        kind = kind or node
        base = self.declaration.for_position(context.position) if context else self.declaration.for_node(kind)
        settings = base['node']
        composite = context and context.composition
        maximum = call_attempts(settings) if retry else 1
        implementation = self.implementation_for(kind, operation, sources)
        declaration = {**base, 'implementation': implementation}
        if context:
            declaration['operation'] = context.definition
        input_hash = digest(input)
        with self.lock:
            index = self.index()
            nodes = index[context.parent].children if context and context.parent else self.record.nodes
            if node in index and node not in nodes:
                raise ValueError(f'Duplicate node identity in tree: {node}')
            for dep in depends_on:
                if dep not in index or index[dep].outcome.status != 'completed':
                    raise ValueError(f'Missing accepted dependency: {dep}')
            upstream = {dep: index[dep].output for dep in depends_on}
            # Include dependency outputs even when callers omit them from their input.
            declaration['dependencies'] = upstream
            declaration_hash = digest(declaration)
            key = (node, input_hash, declaration_hash)
            artifact = self.retain(Artifact.create('node-input/v1', input))
            old = nodes.get(node)
            same = old and old.input_sha256 == input_hash and old.declaration_sha256 == declaration_hash
            if old and not same:
                changed = 'input' if old.input_sha256 != input_hash else 'implementation, settings or dependencies'
                raise NewRunRequired(f'Node {node!r}: {changed} changed; use a new run directory. '
                                     'Keep the prior directory; use reuse_from with rerun_from for selective correction')
            attempts, errors = (old.attempts, old.errors) if same else (0, [])
            rec = NodeRecord(node=node, kind=kind, input=artifact.ref(), input_sha256=input_hash,
                declaration=declaration, declaration_sha256=declaration_hash, implementation=implementation,
                depends_on=list(depends_on), outcome=Outcome(status='needs_correction', reason='Step unfinished'),
                attempts=attempts, errors=errors, children=old.children if old else {},
                position=context.position if context else '', purpose=context.purpose if context else '',
                composition=composite or '', details=old.details if old else {})
            if context:
                self.record = self.record.model_copy(update={'record_type': 'mavc-run/v2'})
            if not composite and key in self.cache:
                cached = self.cache[key]
                output = resolve(self.folder, cached.get('output', cached))
                nodes[node] = rec.model_copy(update={'output': output.ref(), 'reused': True,
                    'outcome': Outcome(status='completed'), 'details': cached.get('details', rec.details)})
                self.save()
                return output.value
            if not composite and node in self.protected:
                raise ProtectedReuseMiss(f'{node}: completed in the reused run but its input or declaration changed; '
                                         'refusing to call it again. Use a new run directory with rerun_from selecting this node')
            retry_type = ToolFailure if settings.get('retry_on') == 'tool_failure' else RetryableFailure
            if not composite and same and old.errors and old.errors[-1]['type'] != retry_type.__name__:
                raise RetainedFailure(node, old.outcome)
            if not composite and attempts >= maximum:
                raise RetainedFailure(node, old.outcome)
            nodes[node] = rec
            self.record = self.record.model_copy(update={'outputs': None,
                'outcome': Outcome(status='needs_correction', reason='Run unfinished')})
            self.save()

        def invoke():
            nonlocal rec
            with self.lock:
                rec = rec.model_copy(update={'attempts': rec.attempts + (0 if composite else 1)})
                nodes[node] = rec
                self.save()  # never reset an attempt budget at resume
            try:
                value = operation()
                if composite:
                    if set(rec.children) != set(context.seen) or (old and old.outcome.status == 'completed'
                            and set(old.children) != set(context.seen)):
                        raise NewRunRequired(f'Node {node!r}: child inventory changed; use a new run directory')
                    rec = rec.model_copy(update={'children': {name: rec.children[name] for name in
                        sorted(context.seen, key=context.seen.get)}})
                    if not complete_children(rec):
                        raise ValueError('Composition has unfinished required children')
                return value
            except Exception as exc:
                with self.lock:
                    rec = rec.model_copy(update={'outcome': failure_outcome(exc),
                        'errors': [*rec.errors, {'type': type(exc).__name__, 'reason': str(exc), 'at': now()}]})
                    nodes[node] = rec
                    self.record = self.record.model_copy(update={'outcome': rec.outcome})
                    self.save()
                raise

        delay = settings.get('retry', {}).get('initial_interval', 0)
        value = invoke() if composite else Retrying(stop=stop_after_attempt(maximum - attempts), wait=wait_fixed(delay),
            retry=retry_if_exception_type(retry_type), reraise=True)(invoke)
        output = self.retain(Artifact.create(output_type, value, [artifact.sha256,
            *[r['sha256'] for r in upstream.values()], *[n.output['sha256'] for n in rec.children.values() if n.output]]))
        with self.lock:
            if not composite:
                self.cache[key] = {'output': output.ref(), 'details': rec.details}
            nodes[node] = rec.model_copy(update={'output': output.ref(), 'outcome': Outcome(status='completed')})
            self.save()
        return value

    def fanout(self, items: Iterable[Any], operation: Callable[[Any], Any], *, max_concurrency: int | None = None) -> list[Any]:
        with ThreadPoolExecutor(max_workers=max_concurrency or self.declaration.execution.get('max_concurrency', 1)) as pool:
            return list(pool.map(operation, items))

    def finish(self, output: Any, *, type: str = 'flow-output/v1', outcome: Outcome | None = None) -> None:
        outcome = outcome or Outcome(status='completed')
        if outcome.status == 'completed' and (not self.record.nodes or any(
                n.outcome.status != 'completed' for n in self.record.nodes.values())):
            raise ValueError('Cannot complete a run with unfinished nodes')
        artifact = self.retain(Artifact.create(type, output, [n.output['sha256'] for n in self.record.nodes.values() if n.output]))
        self.record = self.record.model_copy(update={'outputs': artifact.ref(), 'outcome': outcome})
        self.save()

    def close(self) -> None:
        self.invocation['ended_at'] = now()
        self.save()
        self.cache.close()

    def __enter__(self) -> Runner:
        return self

    def __exit__(self, typ: Any, error: BaseException | None, traceback: Any) -> None:
        if isinstance(error, NewRunRequired):
            self.record = self.original
        elif error:
            self.record = self.record.model_copy(update={'outcome': failure_outcome(error), 'outputs': None})
        self.close()
