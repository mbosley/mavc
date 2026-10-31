"""Real source-file edits and content-cache behavior; no donor or model calls."""
from functools import partial
from pathlib import Path
from types import ModuleType

import pytest
from mavc import Runner, verify
from mavc.fingerprint import fingerprint
from mavc.record import read_json
from mavc.runner import NewRunRequired

CONFIG = {'flow_id': 'fingerprint-test/v1', 'nodes': {'a': {}, 'b': {}}}


def load(path, text):
    path.write_text(text)
    module = ModuleType('example_science')
    module.__file__ = str(path)
    exec(compile(text, str(path), 'exec'), module.__dict__)
    return module


def flow(folder, operation, *, value=1, sources=(), reuse=None):
    with Runner(folder, CONFIG, reuse_from=reuse) as runner:
        a = runner.step('a', value, operation, sources=sources)
        b = runner.step('b', {}, lambda: a + 1, depends_on=['a'])
        runner.finish(b)
    return read_json(folder/'record.json')


def test_automatic_input_code_resume_and_dependencies(tmp_path):
    path = tmp_path/'science.py'
    first = load(path, 'calls=[]\ndef compute():\n calls.append(1)\n return 2\n')
    folder = tmp_path/'run-v1'
    original = flow(folder, first.compute)
    resumed = flow(folder, first.compute)
    assert first.calls == [1] and all(n['reused'] for n in resumed['nodes'].values())
    assert original['nodes']['a']['implementation'] == resumed['nodes']['a']['implementation']
    before = (folder/'record.json').read_bytes()
    with pytest.raises(NewRunRequired):
        flow(folder, first.compute, value=4)
    assert (folder/'record.json').read_bytes() == before
    changed_input = flow(tmp_path/'input-v2', first.compute, value=4, reuse=folder)
    assert first.calls == [1, 1] and not changed_input['nodes']['a']['reused']
    assert not changed_input['nodes']['b']['reused']  # Changed input lineage changes the dependency ref.
    second = load(path, 'calls=[]\ndef compute():\n calls.append(1)\n return 3\n')
    with pytest.raises(NewRunRequired):
        flow(folder, second.compute)
    assert (folder/'record.json').read_bytes() == before
    changed_code = flow(tmp_path/'code-v2', second.compute, reuse=folder)
    assert second.calls == [1] and not any(n['reused'] for n in changed_code['nodes'].values())
    assert changed_code['nodes']['b']['declaration']['dependencies'] != resumed['nodes']['b']['declaration']['dependencies']
    assert all(verify(tmp_path/name)['completed'] for name in ('run-v1','input-v2','code-v2'))


@pytest.mark.parametrize('boundary', ['callable', 'module', 'path', 'shared'])
def test_explicit_helper_sources_invalidate(tmp_path, boundary):
    helper_path = tmp_path/'helper.py'
    helper = load(helper_path, 'VALUE=1\ndef helper(): return VALUE\n')
    operation = lambda: helper.helper()
    source = {'callable':helper.helper, 'module':helper, 'path':helper_path, 'shared':helper_path}[boundary]
    folder = tmp_path/'helper-v1'
    def run(destination, reuse=None):
        with Runner(destination, CONFIG, sources=(source,) if boundary=='shared' else (), reuse_from=reuse) as runner:
            value = runner.step('a', {}, operation, sources=() if boundary=='shared' else (source,))
            runner.finish(value)
    run(folder); run(folder)
    before = (folder/'record.json').read_bytes()
    helper_path.write_text('VALUE=2\ndef helper(): return VALUE\n')
    helper.VALUE = 2
    with pytest.raises(NewRunRequired): run(folder)
    assert (folder/'record.json').read_bytes() == before
    corrected = tmp_path/'helper-v2'; run(corrected, folder)
    assert not read_json(corrected/'record.json')['nodes']['a']['reused']
    assert verify(folder)['completed'] and verify(corrected)['completed']


def test_callable_selection_and_move(tmp_path):
    text = 'def first(): return 1\ndef second(): return 2\nleft,right = lambda: 3, lambda: 4\n'
    source = load(tmp_path/'selection.py', text)
    folder = tmp_path/'selection'
    with Runner(folder, CONFIG) as runner:
        runner.step('a', {}, source.first)
    with pytest.raises(NewRunRequired):
        with Runner(folder, CONFIG) as runner: runner.step('a', {}, source.second)
    assert fingerprint(source.left) != fingerprint(source.right)
    moved = tmp_path/'moved'; moved.mkdir()
    copy = load(moved/'selection.py', text)
    assert fingerprint(source.first) == fingerprint(copy.first)
    assert fingerprint(partial(source.first)) == fingerprint(source.first)
    assert not any(str(tmp_path) in key for key in fingerprint(copy.first))


def test_unavailable_source_needs_explicit_boundary(tmp_path):
    with pytest.raises(ValueError, match='No defining source.*sources='):
        fingerprint(len)
    source = tmp_path/'native-boundary.txt'; source.write_text('declared native implementation v1')
    assert fingerprint(len, (source,)) != fingerprint(max, (source,))
    before = fingerprint(len, (source,)); source.write_text('declared native implementation v2')
    assert fingerprint(len, (source,)) != before
    with Runner(tmp_path/'native', CONFIG) as runner:
        assert runner.step('a', [1,2], partial(len, [1,2]), sources=(source,)) == 2


def test_manual_legacy_with_step_sources_resumes(tmp_path):
    source = tmp_path/'helper.txt'; source.write_text('helper v1')
    def run():
        with Runner(tmp_path/'legacy', CONFIG, implementations={'a':{'frozen.py':'v1'}}) as runner:
            return runner.step('a', 1, lambda: 2, sources=(source,))
    assert run() == run() == 2
    before = (tmp_path/'legacy/record.json').read_bytes()
    with pytest.raises(NewRunRequired):
        Runner(tmp_path/'legacy', CONFIG, implementations={'a':{'frozen.py':'v2'}})
    assert (tmp_path/'legacy/record.json').read_bytes() == before
    source.write_text('helper v2')
    with pytest.raises(NewRunRequired): run()
