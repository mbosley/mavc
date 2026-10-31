import importlib
import sys
from pathlib import Path
from types import ModuleType

import pytest
from pydantic import BaseModel, Field, ValidationError, model_validator

from mavc import Runner, Rejected, RetryableFailure, verify
from mavc.declare import Declaration
from mavc.operation import Operation, sequence, bundle, for_each, iterate, route
from mavc.record import Artifact, read_json, resolve, write_json
from mavc.runner import NewRunRequired
from mavc.route import RouteFailure
from mavc.tree import walk_nodes


class Add(Operation):
    """Add one to an explicit integer."""
    class Input(BaseModel):
        value: int = Field(description='The current integer.')
    Output = Input

    def run(self, children, input):
        return {'value': input.value + 1}


def run(folder, op, input, *, positions=None, reuse=None, rerun=None):
    with Runner(folder, {'flow_id': 'operations/v1', 'positions': positions or {}}, reuse_from=reuse, rerun_from=rerun) as runner:
        value = op(runner, input)
        runner.finish(value.model_dump() if isinstance(value, BaseModel) else value)
    return read_json(folder/'record.json')


def test_composition_resume_and_recursive_verification(tmp_path):
    calls = []
    op = Operation('Increment', run=lambda c, value: calls.append(value) or value + 1)
    flow = sequence('Method', [bundle('Pair', {'left': op, 'right': op})])
    run(tmp_path, flow, 1)
    resumed = run(tmp_path, flow, 1)
    assert calls == [1, 1]
    nodes = dict(walk_nodes(resumed))
    assert resumed['record_type'] == 'mavc-run/v2'
    assert all(n['attempts'] == 0 for n in nodes.values() if n['composition'])
    assert all(n['reused'] for _, n in walk_nodes(resumed, leaves_only=True))
    assert verify(tmp_path)['leaves'] == 2 and verify(tmp_path)['composites'] == 2


@pytest.mark.parametrize('position', [{'model': 'fixed'}, {'legacy': {'model': 'fixed', 'tools': []}}])
def test_effective_partial_settings_and_explicit_legacy(tmp_path, position):
    run(tmp_path, Add(), {'value': 1}, positions={'Add': position})
    saved = read_json(tmp_path/'declaration.json')['positions']['Add']
    assert verify(tmp_path)['completed']
    effective = Declaration(flow_id='x', positions={'Add': position}).for_position('Add')['node']
    assert read_json(tmp_path/'record.json')['nodes']['Add']['declaration']['node'] == effective
    if 'legacy' in position:
        assert effective == position['legacy'] and saved == position
    else:
        assert saved == effective and effective['settings']['tools'] == []
        assert effective['timeout_seconds'] == 600 and not effective['pi_retry']['agent_enabled']
    with pytest.raises(ValidationError):
        Declaration.model_validate({'flow_id': 'x', 'positions': {'Add': {'reasioning': 'high'}}})
    with pytest.raises(ValidationError):
        Declaration(flow_id='x', positions={'Add': {'retry': {'max_attemps': 2}}})
    with pytest.raises(ValueError, match='override'):
        Declaration(flow_id='x', positions={'Add': {'model': 'a', 'legacy': {'model': 'b'}}}).for_position('Add')
    with pytest.raises(NewRunRequired):
        run(tmp_path, Add(), {'value': 2}, positions={'Add': position})
    with pytest.raises(NewRunRequired):
        run(tmp_path, Add(), {'value': 1}, positions={'Add': {'model': 'different'}})


def test_parent_inventory_cannot_omit_or_add_completed_children(tmp_path):
    names = ['a', 'b']
    child = Add()
    parent = Operation('Inventory', composition='custom', run=lambda c, value:
                       {name: c(name, child, value).model_dump() for name in names})
    run(tmp_path, parent, {'value': 1})
    original = (tmp_path/'record.json').read_bytes()
    for changed in (['a'], ['a', 'b', 'c']):
        names[:] = changed
        with pytest.raises(NewRunRequired, match='inventory'):
            run(tmp_path, parent, {'value': 1})
        assert (tmp_path/'record.json').read_bytes() == original


def test_interrupted_parent_reenters_and_leaf_attempt_budget_stays(tmp_path):
    calls = []
    def work(c, value):
        calls.append(value)
        if len(calls) == 1:
            raise KeyboardInterrupt()
        return value
    leaf = Operation('Work', run=work)
    leaf.retry = True
    parent = bundle('Resume', {'first': Add(), 'second': leaf})
    positions = {'Resume.Work': {'maximum_attempts': 2}}
    with pytest.raises(KeyboardInterrupt):
        run(tmp_path, parent, {'value': 1}, positions=positions)
    record = run(tmp_path, parent, {'value': 1}, positions=positions)
    nodes = dict(walk_nodes(record))
    assert len(calls) == 2 and nodes['Resume/first']['reused']
    assert nodes['Resume/second']['attempts'] == 2 and verify(tmp_path)['completed']


def test_empty_foreach_stable_ids_parallel_context_and_iteration(tmp_path):
    op = for_each('Items', Add(), key=lambda row: row['id'])
    run(tmp_path/'empty', op, [], positions={'Items': {'max_concurrency': 2}})
    assert verify(tmp_path/'empty')['completed'] and verify(tmp_path/'empty')['leaves'] == 0
    record = run(tmp_path/'parallel', op, [{'id': 'b', 'value': 1}, {'id': 'a', 'value': 2}],
                 positions={'Items': {'max_concurrency': 2}})
    children = record['nodes']['Items']['children']
    assert list(children) == ['Items/b', 'Items/a']
    assert {node['position'] for node in children.values()} == {'Items.Add'}
    assert verify(tmp_path/'parallel')['completed']
    repeated = iterate('Until', Add(), bound=5, stop=lambda row: row.value == 3)
    record = run(tmp_path/'iterate', repeated, {'value': 0})
    assert record['nodes']['Until']['details'] == {'stopped': 'condition', 'iterations': 3}
    assert verify(tmp_path/'iterate')['completed']


def test_route_failed_local_visible_and_failed_frontier_not_rescued(tmp_path):
    def fail(c, value):
        raise Rejected('local abstention/failure')
    local = Operation('Local', run=fail)
    frontier = Operation('Frontier', run=lambda c, value: 'answer')
    flow = route('Choose', local, frontier)
    record = run(tmp_path/'accepted', flow, 1, positions={'Choose': {'cutoff': .8}})
    parent = record['nodes']['Choose']
    assert parent['details']['selected'] == 'Choose/frontier'
    assert parent['children']['Choose/local']['outcome']['status'] == 'rejected'
    assert parent['children']['Choose/local']['errors'][0]['reason'] == 'local abstention/failure'
    assert verify(tmp_path/'accepted')['completed']
    run(tmp_path/'accepted', flow, 1, positions={'Choose': {'cutoff': .8}})
    frontier = Operation('Frontier', run=lambda c, value: (_ for _ in ()).throw(RuntimeError('frontier failed')))
    with pytest.raises(RouteFailure) as failed:
        run(tmp_path/'failed', route('Choose', local, frontier), 1, positions={'Choose': {'cutoff': .8}})
    parent = read_json(tmp_path/'failed/record.json')['nodes']['Choose']
    result = parent['details']['route']
    assert parent['details']['selected'] == 'Choose/frontier'
    assert result == vars(failed.value.result)
    assert result['model'] == 'Frontier' and result['cutoff'] == .8 and result['confidence'] is None
    assert result['fallback_reason'] == 'local_failure' and result['local_error'] == 'Rejected: local abstention/failure'
    assert result['status'] == 'failed' and result['error'] == 'RuntimeError: frontier failed'
    assert parent['children']['Choose/local']['outcome']['status'] == 'rejected'
    assert parent['children']['Choose/frontier']['outcome']['status'] == 'failed'
    assert not verify(tmp_path/'failed')['completed']


def test_fresh_output_validates_once_and_matches_saved_value(tmp_path):
    validations = []
    class Normalized(BaseModel):
        value: int
        @model_validator(mode='after')
        def normalize(self):
            validations.append(self.value)
            self.value += 1
            return self
    op = Operation('Normalize', run=lambda c, value: {'value': value})
    op.Output = Normalized
    with Runner(tmp_path, {'flow_id': 'once'}) as runner:
        result = op(runner, 1)
        saved = resolve(tmp_path, runner.index()['Normalize'].output).value
        assert validations == [1] and result.model_dump() == saved == {'value': 2}
        runner.finish(saved)
    original = read_json(tmp_path/'record.json')['nodes']['Normalize']['output']
    retained = tmp_path/'outputs/objects'/(original['sha256'] + '.json')
    original_bytes = retained.read_bytes()
    with pytest.raises(ValueError, match='validators must be idempotent'):
        with Runner(tmp_path, {'flow_id': 'once'}) as runner:
            op(runner, 1)
    assert validations == [1, 2] and retained.read_bytes() == original_bytes
    assert read_json(tmp_path/'record.json')['nodes']['Normalize']['output'] == original
    assert resolve(tmp_path, original).value == saved
    cached = tmp_path/'deterministic'
    first = run(cached, Add(), {'value': 1})
    resumed = run(cached, Add(), {'value': 1})
    assert first['outputs'] == resumed['outputs'] and resumed['nodes']['Add']['reused']


def test_evidence_survives_typed_rejection_and_distinct_parallel_calls(tmp_path, monkeypatch):
    module = ModuleType('mavc.model')
    module._caller = lambda op: test_evidence_survives_typed_rejection_and_distinct_parallel_calls
    module.sources = lambda op: (test_evidence_survives_typed_rejection_and_distinct_parallel_calls,)
    def execute(op, context, input):
        evidence = context.runner.retain(Artifact.create('call/v1', {'request': input.model_dump(), 'raw': 'bad'}))
        context.evidence(evidence.ref())
        return {'value': 'bad'} if input.value < 0 else input.model_dump()
    module.execute = execute
    monkeypatch.setitem(sys.modules, 'mavc.model', module)
    class Typed(Add):
        run = Operation.run
    with pytest.raises(ValidationError):
        run(tmp_path/'bad', Typed(), {'value': -1})
    leaf = read_json(tmp_path/'bad/record.json')['nodes']['Typed']
    assert leaf['output'] is None and leaf['details']['evidence']
    assert resolve(tmp_path/'bad', leaf['details']['evidence'][0]).value['raw'] == 'bad'
    assert not verify(tmp_path/'bad')['completed']
    parallel = for_each('Calls', Typed(), key=lambda row: row['value'])
    record = run(tmp_path/'parallel', parallel,
                 [{'value': 1}, {'value': 2}], positions={'Calls': {'max_concurrency': 2}})
    evidence = [n['details']['evidence'][0] for _, n in walk_nodes(record, leaves_only=True)]
    assert len({ref['sha256'] for ref in evidence}) == 2 and verify(tmp_path/'parallel')['completed']
    original = (tmp_path/'parallel/record.json').read_bytes()
    reused = run(tmp_path/'correction', parallel, [{'value': 1}, {'value': 2}],
                 positions={'Calls': {'max_concurrency': 2}}, reuse=tmp_path/'parallel', rerun='Calls/2')
    leaves = dict(walk_nodes(reused, leaves_only=True))
    assert leaves['Calls/1']['reused'] and not leaves['Calls/2']['reused']
    for _, node in walk_nodes(reused, leaves_only=True):
        assert resolve(tmp_path/'correction', node['details']['evidence'][0]).value['raw'] == 'bad'
    assert (tmp_path/'parallel/record.json').read_bytes() == original and verify(tmp_path/'correction')['completed']


def test_learn_custom_resolver_stays_stdlib_and_skips_composites(tmp_path, monkeypatch):
    root = Path(__file__).parents[2]/'src/mavc'
    alias = ModuleType('stdlib_mavc'); alias.__path__ = [str(root)]
    learn = ModuleType('stdlib_mavc.learn'); learn.__path__ = [str(root/'learn')]
    monkeypatch.setitem(sys.modules, 'stdlib_mavc', alias)
    monkeypatch.setitem(sys.modules, 'stdlib_mavc.learn', learn)
    data = importlib.import_module('stdlib_mavc.learn.data')
    run(tmp_path, bundle('Method', {'a': Add()}), {'value': 1})
    candidates = list(data.collect([tmp_path], resolver=lambda folder, ref: resolve(folder, ref).value))
    assert [c.metadata['node'] for c in candidates] == ['Method/a']
    assert 'stdlib_mavc.record' not in sys.modules


def test_selective_nested_rerun_and_explicit_composite_subtree(tmp_path):
    calls = []
    leaf = Operation('Leaf', run=lambda c, value: calls.append(c.node) or value)
    pair = bundle('Pair', {'a': leaf, 'b': leaf})
    flow = Operation('Document', composition='custom', run=lambda c, value:
        {'left': c('left', pair, value), 'right': c('right', leaf, value),
         'consumer': c('consumer', leaf, value, depends_on=['Document/left'])})
    run(tmp_path/'original', flow, 1)
    original = (tmp_path/'original/record.json').read_bytes()
    calls.clear()
    record = run(tmp_path/'leaf', flow, 1, reuse=tmp_path/'original', rerun='Document/left/a')
    assert calls == ['Document/left/a', 'Document/consumer']
    leaves = dict(walk_nodes(record, leaves_only=True))
    assert leaves['Document/left/b']['reused'] and leaves['Document/right']['reused']
    calls.clear()
    run(tmp_path/'subtree', flow, 1, reuse=tmp_path/'original', rerun='Document/left')
    assert calls == ['Document/left/a', 'Document/left/b', 'Document/consumer']
    assert (tmp_path/'original/record.json').read_bytes() == original
    assert verify(tmp_path/'leaf')['completed'] and verify(tmp_path/'subtree')['completed']


def test_actual_user_body_helper_and_signature_changes_invalidate(tmp_path, monkeypatch):
    path = tmp_path/'science.py'
    helper = tmp_path/'external.txt'; helper.write_text('science v1')
    code = ('from mavc.operation import Operation\nfrom pydantic import BaseModel\n'
            'class User(Operation):\n class Input(BaseModel):\n  value:int\n Output=Input\n'
            ' def run(self,c,input): return {"value":input.value+1}\n')
    def load(text):
        path.write_text(text)
        module = ModuleType('user_method'); module.__file__ = str(path)
        monkeypatch.setitem(sys.modules, 'user_method', module)
        exec(compile(text, str(path), 'exec'), module.__dict__)
        return module.User(sources=(helper,))
    leaf = load(code)
    flow = bundle('Parent', {'work': leaf})
    run(tmp_path/'v1', flow, {'value': 1}); run(tmp_path/'v1', flow, {'value': 1})
    original = (tmp_path/'v1/record.json').read_bytes()
    helper.write_text('science v2')
    with pytest.raises(NewRunRequired): run(tmp_path/'v1', flow, {'value': 1})
    helper.write_text('science v1')
    changed = bundle('Parent', {'work': load(code.replace('+1', '+2'))})
    with pytest.raises(NewRunRequired): run(tmp_path/'v1', changed, {'value': 1})
    assert (tmp_path/'v1/record.json').read_bytes() == original
    run(tmp_path/'v2', changed, {'value': 1}, reuse=tmp_path/'v1')
    class Different(BaseModel):
        value: int
        description: str = 'new signature'
    # Child signature change is checked even while the parent body traverses.
    leaf.Output = Different
    path.write_text(code)
    with pytest.raises(NewRunRequired): run(tmp_path/'v1', flow, {'value': 1})
    assert verify(tmp_path/'v1')['completed'] and verify(tmp_path/'v2')['completed']


def test_opaque_body_requires_explicit_source_and_leaf_cannot_hide_children(tmp_path):
    opaque = eval(compile('lambda c,v:v+1', '<opaque>', 'eval'))
    with pytest.raises(ValueError, match='source'):
        run(tmp_path/'missing', Operation('Opaque', run=opaque), 1)
    boundary = tmp_path/'boundary.txt'; boundary.write_text('native method v1')
    run(tmp_path/'explicit', Operation('Opaque', run=opaque, sources=(boundary,)), 1)
    boundary.write_text('native method v2')
    with pytest.raises(NewRunRequired):
        run(tmp_path/'explicit', Operation('Opaque', run=opaque, sources=(boundary,)), 1)
    leaf = Operation('FalseLeaf', run=lambda c,v:c('child', Add(), {'value': v}))
    with pytest.raises(ValueError, match='declare composition'):
        run(tmp_path/'false-leaf', leaf, 1)


def test_cached_output_restores_strict_tuple_fields_from_json(tmp_path):
    class Draft(BaseModel, strict=True):
        span: tuple[int, int]
        labels: tuple[str, ...]
    calls = []
    op = Operation('Draft', run=lambda c, value: calls.append(value) or Draft(span=(1, 2), labels=('a',)))
    op.Output = Draft
    with Runner(tmp_path, {'flow_id': 'tuples'}) as runner:
        first = op(runner, 1)
        runner.finish(first.model_dump(mode='json'))
    with Runner(tmp_path, {'flow_id': 'tuples'}) as runner:
        restored = op(runner, 1)
    assert calls == [1] and restored == first and restored.span == (1, 2)
