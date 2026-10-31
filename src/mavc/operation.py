"""Recursive methods: Python child calls, typed leaves and position settings."""
from __future__ import annotations
from typing import Any, Callable, Iterable, Mapping, TYPE_CHECKING
if TYPE_CHECKING:
    from .runner import Runner
    from .declare import Declaration
import json
from dataclasses import asdict
from pydantic import BaseModel

from .fingerprint import fingerprint
from .route import Prediction, RouteFailure, route as select_route


def json_value(value: Any) -> Any:
    return value.model_dump(mode='json') if isinstance(value, BaseModel) else value


class Operation:
    Input: type[BaseModel] | None = None
    Output: type[BaseModel] | None = None
    composition = ''
    sources = ()
    caller = None
    retry = False
    operations: tuple[Operation, ...] = ()

    def __init__(self, name: str | None = None, *, run: Callable[[Children, Any], Any] | None = None, composition: str | None = None, sources: tuple = (), structure: dict | None = None, caller: Callable[[dict], dict] | None = None) -> None:
        self.name = name or type(self).__name__
        self.body = run
        self.composition = self.composition if composition is None else composition
        self.sources = (*self.sources, *sources)
        self.structure = structure or {}
        self.caller = caller or self.caller

    def run(self, children: Children, input: Any) -> Any:
        if self.body is not None:
            return self.body(children, input)
        from .model import execute
        return execute(self, children, input)

    def validate(self, declaration: Declaration | dict, *, position: str | None = None) -> None:
        """Validate model settings in this operation and known composite children."""
        from .declare import Declaration
        declaration = Declaration.model_validate(declaration)
        position = position or self.name
        if self.body is None and type(self).run is Operation.run:
            declaration.validate_model(position)
        for operation in self.operations:
            operation.validate(declaration, position=position + '.' + operation.name)

    def __call__(self, runner: Runner, input: Any, *, name: str | None = None, node: str | None = None, kind: str | None = None, depends_on: Iterable[str] = (), _parent: str | None = None, _position: str | None = None) -> Any:
        if _parent is None:
            self.validate(runner.declaration)
        input = self.Input.model_validate(input) if self.Input else input
        context = Children(runner, node or name or self.name, _parent, _position or self.name, self)
        sources = (type(self), *self.sources)
        context.definition['body'] = fingerprint(self.body or self.run, self.sources)
        if self.body is None and type(self).run is Operation.run:
            from .model import _caller, sources as model_sources
            context.definition['caller'] = fingerprint(_caller(self), self.sources)
            sources += model_sources(self)
        typed = None
        def invoke():
            nonlocal typed
            value = self.run(context, input)
            typed = self.Output.model_validate(value) if self.Output else value
            return json_value(typed)
        value = runner.step(context.node, json_value(input), invoke, kind=kind or self.name,
                            sources=sources, depends_on=depends_on, retry=self.retry, context=context)
        if self.Output and typed is None:
            # Retained output is JSON, so it is read as JSON: arrays restore strict tuple fields as live parsing does.
            typed = self.Output.model_validate_json(json.dumps(value))
            if json_value(typed) != value:
                raise ValueError('Cached Output validation changed retained JSON; validators must be idempotent')
        return typed if typed is not None else value


class Children:
    def __init__(self, runner: Runner, node: str, parent: str | None, position: str, operation: Operation) -> None:
        self.runner, self.node, self.parent, self.position = runner, node, parent, position
        self.composition = operation.composition
        self.purpose = (type(operation).__doc__ or '').strip()
        self.definition = {'name': operation.name, 'purpose': self.purpose, 'composition': self.composition,
            'signature': {name: model.model_json_schema() for name, model in
                          [('input', operation.Input), ('output', operation.Output)] if model}, **operation.structure}
        self.settings = runner.declaration.for_position(position)['node']
        if self.settings.get('tool_manifest'):
            self.definition['tools'] = self.settings['tool_manifest']
        self.seen = {}

    @property
    def details(self) -> dict:
        return self.runner.index()[self.node].details

    def evidence(self, ref: dict) -> None:
        with self.runner.lock:
            self.details.setdefault('evidence', []).append(ref)
            self.runner.save()

    def __call__(self, name: str, operation: Operation, input: Any, *, node: str | None = None, kind: str | None = None, depends_on: Iterable[str] = (), order: int | None = None) -> Any:
        if not self.composition:
            raise ValueError('An Operation calling children must declare composition')
        node = node or self.node + '/' + str(name)
        with self.runner.lock:
            if node in self.seen:
                raise ValueError(f'Duplicate child instance: {node}')
            self.seen[node] = len(self.seen) if order is None else order
        return operation(self.runner, input, node=node, kind=kind, depends_on=depends_on,
                         _parent=self.node, _position=self.position + '.' + operation.name)


def _compose(name, form, operations, body, **structure):
    operations = tuple(operations)
    sources = tuple(source for op in operations for source in (type(op), *op.sources))
    result = Operation(name, run=body, composition=form, sources=sources,
                       structure={'available_children': [op.name for op in operations], **structure})
    result.operations = operations
    return result


def sequence(name: str, operations: Iterable[Operation]) -> Operation:
    operations = list(operations)
    def run(children, value):
        for i, op in enumerate(operations):
            value = children(str(i), op, json_value(value),
                             depends_on=[children.node + '/' + str(i - 1)] if i else [])
        return json_value(value)
    return _compose(name, 'sequence', operations, run)


def bundle(name: str, operations: Mapping[str, Operation]) -> Operation:
    operations = dict(operations)
    return _compose(name, 'bundle', operations.values(), lambda c, value:
                    {key: json_value(c(key, op, value)) for key, op in operations.items()}, names=list(operations))


def for_each(name: str, operation: Operation, *, key: Callable[[Any], Any]) -> Operation:
    def run(children, values):
        ids = [str(key(value)) for value in values]
        if len(set(ids)) != len(ids):
            raise ValueError('For-each requires unique scientific IDs')
        return children.runner.fanout(list(enumerate(values)), lambda item:
            json_value(children(ids[item[0]], operation, item[1], order=item[0])),
            max_concurrency=children.settings.get('max_concurrency', 1))
    result = _compose(name, 'for-each', [operation], run)
    result.sources += (key,)
    return result


def iterate(name: str, operation: Operation, *, bound: int, stop: Callable[[Any], bool]) -> Operation:
    if isinstance(bound, bool) or not isinstance(bound, int) or bound < 1:
        raise ValueError('Iteration bound must be a positive integer')
    def run(children, value):
        for i in range(bound):
            value = children(str(i), operation, json_value(value),
                             depends_on=[children.node + '/' + str(i - 1)] if i else [])
            if stop(value):
                children.details.update(stopped='condition', iterations=i + 1)
                return json_value(value)
        children.details.update(stopped='bound', iterations=bound)
        return json_value(value)
    result = _compose(name, 'iterate', [operation], run, bound=bound)
    result.sources += (stop,)
    return result


def route(name: str, local: Operation, frontier: Operation, *, valid: Callable[[Any], bool] = lambda answer: answer is not None) -> Operation:
    def run(children, value):
        try:
            result = select_route(children.node, value,
                local=lambda input: Prediction(**json_value(children('local', local, input))),
                frontier=lambda input: json_value(children('frontier', frontier, input)),
                cutoff=children.settings['cutoff'], local_model=local.name, frontier_model=frontier.name, valid=valid)
        except RouteFailure as exc:
            children.details.update(selected=children.node + '/' + exc.result.route, route=asdict(exc.result))
            raise
        children.details.update(selected=children.node + '/' + result.route, route=asdict(result))
        return asdict(result)
    result = _compose(name, 'route', [local, frontier], run)
    result.sources += (valid,)
    return result
