import copy

import pytest
from mavc import Artifact, Runner, Rejected, RetryableFailure, digest, verify
from mavc.declare import call_attempts
from mavc.record import read_json, write_json
from mavc.runner import ProtectedReuseMiss, RetainedFailure


def declaration():
    return {'flow_id': 'small/v1', 'nodes': {'a': {'model': 'fixed'}, 'b': {}, 'c': {}},
            'context': {'source_sha256': 'source-one'}, 'execution': {'max_concurrency': 2}}


def execute(folder, *, config=None, implementation=None, rerun=None, input=1, reuse=None):
    observed = []
    with Runner(folder, config or declaration(), implementations=implementation or {
            n: {'flow.py': 'version-one'} for n in ['a', 'b', 'c']}, rerun_from=rerun, reuse_from=reuse) as runner:
        def op(name, value):
            observed.append(name)
            return value
        a = runner.step('a', input, lambda: op('a', input + 1))
        b = runner.step('b', a, lambda: op('b', a + 1), depends_on=['a'])
        c = runner.step('c', b, lambda: op('c', b + 1), depends_on=['b'])
        runner.finish(c)
    assert verify(folder)['completed']
    return observed


def test_content_cache_and_downstream_rerun(tmp_path):
    assert execute(tmp_path) == ['a', 'b', 'c']
    assert execute(tmp_path) == []
    corrected = tmp_path / 'corrected'
    assert execute(corrected, rerun='b', reuse=tmp_path) == ['b', 'c']
    assert read_json(corrected / 'record.json')['nodes']['a']['reused']


def test_protected_reuse_refuses_a_cache_miss_before_calling(tmp_path):
    execute(tmp_path / 'first')
    calls = []
    implementations = {n: {'flow.py': 'version-one'} for n in 'abc'}
    with Runner(tmp_path / 'second', declaration(), implementations=implementations,
                reuse_from=tmp_path / 'first', rerun_from='c', protect_reused=True) as runner:
        a = runner.step('a', 1, lambda: calls.append('a') or 2)
        b = runner.step('b', a, lambda: calls.append('b') or a + 1, depends_on=['a'])
        assert runner.step('c', b, lambda: calls.append('c') or b + 1, depends_on=['b']) == 4
    assert calls == ['c']  # reused nodes come from the cache; only the rerun node is called
    with Runner(tmp_path / 'third', declaration(), implementations=implementations,
                reuse_from=tmp_path / 'first', rerun_from='c', protect_reused=True) as runner:
        with pytest.raises(ProtectedReuseMiss):
            runner.step('a', 5, lambda: calls.append('changed a'))  # an accepted node with changed input
    assert calls == ['c']


def test_input_source_declaration_and_code_invalidate(tmp_path):
    first, second, third, fourth, fifth = [tmp_path / str(i) for i in range(5)]
    execute(first)
    assert execute(second, input=4, reuse=first) == ['a', 'b', 'c']
    config = declaration()
    config['nodes']['a']['model'] = 'changed'
    with pytest.raises(ValueError, match='new run directory'):
        execute(second, config=config, input=4)
    assert execute(third, config=config, input=4, reuse=second) == ['a']
    implementation = {n: {'flow.py': 'version-one'} for n in ['a', 'b', 'c']}
    implementation['b']['flow.py'] = 'version-two'
    assert execute(fourth, config=config, implementation=implementation, input=4, reuse=third) == ['b']
    config['context']['source_sha256'] = 'source-two'
    assert execute(fifth, config=config, implementation=implementation, input=4, reuse=fourth) == ['a', 'b', 'c']
    for folder in [first, second, third, fourth, fifth]:
        assert verify(folder)['completed']


def test_retry_budget_not_reset_by_resume(tmp_path):
    config = declaration()
    config['nodes']['a']['maximum_attempts'] = 2
    calls = []
    def fail():
        calls.append(1)
        raise RetryableFailure('known pre-call capacity')
    for iteration in range(2):
        with pytest.raises((RetryableFailure, RetainedFailure)):
            with Runner(tmp_path, config, implementations={'a': {'flow.py': 'v1'}}) as runner:
                runner.step('a', {'request': 'same'}, fail, retry=True)
    assert len(calls) == 2
    assert read_json(tmp_path / 'record.json')['nodes']['a']['attempts'] == 2
    assert verify(tmp_path)['status'] == 'failed'


def test_rejection_keeps_call_and_never_retries(tmp_path):
    config = declaration()
    calls = []
    for _ in range(2):
        with pytest.raises((Rejected, RetainedFailure)):
            with Runner(tmp_path, config, implementations={k: {'flow.py': 'v1'} for k in ['a', 'b']}) as runner:
                raw = runner.step('a', 'request', lambda: calls.append(1) or {'proposal': 'bad'})
                def reject():
                    raise Rejected('literal quote absent')
                runner.step('b', raw, reject, depends_on=['a'])
    assert calls == [1]
    record = read_json(tmp_path / 'record.json')
    assert record['nodes']['a']['outcome']['status'] == 'completed'
    assert record['outcome']['status'] == 'rejected'
    assert verify(tmp_path)['completed'] is False


@pytest.mark.parametrize('inner', [
    {'harness_settings': {'retry': {'enabled': True, 'maxRetries': 3}}},
    {'harness_retry': {'provider_max_retries': 1}},
    {'provider_retry': {'max_retries': 2}},
])
def test_single_retry_owner(inner):
    with pytest.raises(ValueError, match='one outer'):
        call_attempts({'maximum_attempts': 2, **inner})
    assert call_attempts({'maximum_attempts': 1, **inner}) == 1


def test_unknown_completion_and_science_do_not_retry(tmp_path):
    config = declaration()
    config['nodes']['a']['maximum_attempts'] = 3
    calls = []
    with pytest.raises(RuntimeError, match='unknown'):
        with Runner(tmp_path, config, implementations={'a': {'flow.py': 'v1'}}) as runner:
            def fail():
                calls.append(1)
                raise RuntimeError('remote completion unknown')
            runner.step('a', 'request', fail, retry=True)
    assert calls == [1]


def test_decimal_records_and_corruption(tmp_path):
    value = Artifact.create('probability/v1', {'p': 0.93})
    assert Artifact.model_validate(value.model_dump()).value == {'p': 0.93}
    with pytest.raises(ValueError):
        Artifact.model_validate({**value.model_dump(), 'value': {'p': 0.9}})
    with pytest.raises(ValueError):
        digest({'p': float('nan')})
    execute(tmp_path)
    rec = read_json(tmp_path / 'record.json')
    rec['nodes']['b']['depends_on'] = ['absent']
    write_json(tmp_path / 'record.json', rec)
    with pytest.raises(ValueError, match='missing/failed dependency'):
        verify(tmp_path)


def test_parallel_failure_does_not_complete(tmp_path):
    with pytest.raises(Rejected):
        with Runner(tmp_path, declaration(), implementations={k: {'flow.py': 'v1'} for k in ['a', 'b']}) as runner:
            def work(n):
                def operation():
                    if n == 'b':
                        raise Rejected('invalid evidence')
                    return 1
                return runner.step(n, n, operation)
            runner.fanout(['a', 'b'], work)
    record = read_json(tmp_path / 'record.json')
    assert record['nodes']['a']['outcome']['status'] == 'completed'
    assert record['outcome']['status'] == 'rejected'
    assert not verify(tmp_path)['completed']


def test_correction_preserves_rejected_record_proposal_and_errors(tmp_path):
    original, corrected = tmp_path / 'rejected', tmp_path / 'corrected'
    config = declaration()
    implementation = {k: {'flow.py': 'v1'} for k in ['a', 'b']}
    with pytest.raises(Rejected):
        with Runner(original, config, implementations=implementation) as runner:
            proposal = runner.step('a', 'request', lambda: {'proposal': 'missing quote'})
            def bad_check():
                raise Rejected('quote absent from source')
            runner.step('b', proposal, bad_check, depends_on=['a'])
    prior_bytes = (original / 'record.json').read_bytes()
    with Runner(corrected, config, implementations=implementation, reuse_from=original, rerun_from='a') as runner:
        proposal = runner.step('a', 'request', lambda: {'proposal': 'literal source quote'})
        accepted = runner.step('b', proposal, lambda: proposal, depends_on=['a'])
        runner.finish(accepted)
    assert (original / 'record.json').read_bytes() == prior_bytes
    from mavc.record import resolve
    prior = read_json(original / 'record.json')
    assert resolve(original, prior['nodes']['a']['output']).value == {'proposal': 'missing quote'}
    assert prior['nodes']['b']['errors'][0]['reason'] == 'quote absent from source'
    assert verify(original)['status'] == 'rejected'
    assert verify(corrected)['completed']


def test_changed_partial_method_preserves_prior_record(tmp_path):
    config = declaration()
    with pytest.raises(Rejected):
        with Runner(tmp_path, config, implementations={'a': {'flow.py': 'v1'}}) as runner:
            def reject():
                raise Rejected('keep rejected method independently inspectable')
            runner.step('a', 'old input', reject)
    before = (tmp_path / 'record.json').read_bytes()
    changed = copy.deepcopy(config)
    changed['context']['source_sha256'] = 'changed source'
    with pytest.raises(ValueError, match='new run directory'):
        Runner(tmp_path, changed, implementations={'a': {'flow.py': 'v1'}})
    assert (tmp_path / 'record.json').read_bytes() == before
    assert verify(tmp_path)['status'] == 'rejected'
    with pytest.raises(ValueError, match='new run directory'):
        with Runner(tmp_path, config, implementations={'a': {'flow.py': 'v1'}}) as runner:
            runner.step('a', 'changed input', lambda: 1)
    assert (tmp_path / 'record.json').read_bytes() == before
    assert verify(tmp_path)['status'] == 'rejected'


@pytest.mark.parametrize('status', ['needs_correction', 'needs_human_review'])
def test_noncompleted_outcomes_stay_honest(tmp_path, status):
    from mavc import NeedsCorrection, NeedsHumanReview
    cls = NeedsCorrection if status == 'needs_correction' else NeedsHumanReview
    with pytest.raises(cls):
        with Runner(tmp_path, declaration(), implementations={'a': {'flow.py': 'v1'}}) as runner:
            def stop():
                raise cls('requires further work')
            runner.step('a', {'p': 0.93}, stop)
    assert verify(tmp_path)['status'] == status
