"""Public Pi SDK with its provider-free faux session; never a live model."""
import os
from pathlib import Path
import subprocess
import sys

import pytest
from mavc import Runner, verify
from mavc.adapters.pi import call, PI_VERSION
from mavc.record import show

sdk = pytest.mark.skipif(not os.environ.get('MAVC_PI_ROOT'), reason='Optional Pi SDK: install docs/pi.md runtime and set MAVC_PI_ROOT')


def test_missing_runtime_explains_installation(monkeypatch):
    monkeypatch.delenv('MAVC_PI_ROOT', raising=False)
    with pytest.raises(ValueError, match='@earendil-works/pi-coding-agent@1.4.0'):
        call({})


def run_recorded(folder, turns):
    from examples.pi_agent import CollectionAgent, TOOLS, DOCUMENTS
    flow = CollectionAgent()
    config = {'flow_id': 'pi-sdk-test', 'positions': {'CollectionAgent': {
        'provider': 'faux', 'model': 'recorded-collection', 'reasoning': 'off', 'harness_version': PI_VERSION,
        'harness_settings': {'recorded_turns': turns}, 'settings': {'tools': [tool.name for tool in TOOLS]},
        'tool_manifest': [tool.describe() for tool in TOOLS], 'request': {'terminal_tool': 'submit'}}}}
    with Runner(folder, config, flow=flow) as runner:
        answer = flow(runner, {'question': 'How were the river samples stored?', 'catalog': list(DOCUMENTS)})
        runner.finish(answer.model_dump(mode='json'))
    return show(folder, 'CollectionAgent')


@sdk
@pytest.mark.parametrize('schema_rejection', [False, True])
def test_public_pi_search_read_rejection_continuation_and_checked_submit(tmp_path, schema_rejection):
    from examples.pi_agent import recorded_turns
    turns = recorded_turns()
    if schema_rejection:
        del turns[2]['tools'][0]['input']['quote']
    call_view = run_recorded(tmp_path, turns)
    assert call_view['output']['document_id'] == 'water'
    assert 'amber bottles' in call_view['output']['quote']
    calls = [entry['value'] for entry in call_view['evidence'] if entry['type'] == 'model-tool-call/v1']
    assert [row['name'] for row in calls] == ['search_collection', 'read_document', 'submit', 'submit']
    assert [row['outcome'] for row in calls] == ['accepted', 'accepted', 'rejected', 'accepted']
    assert [row['correction_count'] for row in calls[-2:]] == [0, 1]
    events = [entry['value'] for entry in call_view['evidence'] if entry['type'] == 'model-session-event/v1']
    assert len(events) == 1 and events[0]['followups'] == 1
    evidence = next(entry['value'] for entry in call_view['evidence'] if entry['type'] == 'model-call/v1')
    response = evidence['response']
    assert response['mode'] == 'recorded' and response['observed']['harness_version'] == PI_VERSION
    assert evidence['settings_check']['mismatches'] == []
    assert response['provider_requests'] == []  # Faux has no HTTP request construction.
    assert len(response['session_requests']) == 5
    for request in response['session_requests']:
        assert request['system_prompt'].startswith('Search and read the collection')
        assert '<cwd>' not in request['system_prompt'] and str(Path.cwd()) not in request['system_prompt']
    assert verify(tmp_path)['completed']


@sdk
def test_three_rejected_submissions_fail_without_an_output(tmp_path):
    from examples.pi_agent import recorded_turns
    turns = recorded_turns()
    turns = [*turns[:3], turns[2], turns[2]]
    with pytest.raises(ValueError, match='No checked submission accepted'):
        run_recorded(tmp_path, turns)
    view = show(tmp_path, 'CollectionAgent')
    assert view['output'] is None and show(tmp_path)['output'] is None
    submits = [entry['value'] for entry in view['evidence'] if entry['type'] == 'model-tool-call/v1' and entry['value']['name'] == 'submit']
    assert len(submits) == 3 and submits[-1]['reply']['closed']


@sdk
def test_wrong_version_and_timeout_fail_without_a_provider_call():
    request = {'node': {'provider': 'faux', 'model': 'recorded', 'reasoning': 'off',
                        'harness_settings': {'recorded_turns': []}}, 'harness_version': 'different'}
    with pytest.raises(RuntimeError, match='Adapter supports public Pi 1.4.0'):
        call(request)
    request['node']['timeout_seconds'] = 0.01
    with pytest.raises(RuntimeError, match='Pi call timed out'):
        call(request)


@sdk
def test_pi_agent_example_is_offline_and_writes_a_verified_run(tmp_path):
    example = Path(__file__).parents[1]/'examples/pi_agent.py'
    subprocess.run([sys.executable, '-m', 'mavc', 'run', str(example), str(tmp_path)],
                   check=True, capture_output=True, text=True)
    assert verify(tmp_path)['completed']


@sdk
def test_public_pi_provider_http_is_mocked_and_local_paths_are_not_sent(monkeypatch):
    monkeypatch.setenv('GROQ_API_KEY', 'unused')
    import json
    from mavc.model import check_settings, _failure
    bridge = Path(__file__).parents[1]/'src/mavc/adapters/pi_bridge.mjs'
    result = subprocess.run(['node', str(Path(__file__).with_name('pi_sdk_http.mjs')), str(bridge)],
                            capture_output=True, text=True, check=True)
    value = json.loads(result.stdout)
    assert value['sent'] == 1
    request, response = value['request'], value['response']
    assert response['mode'] == 'live' and response['text'] == '{"answer":"example"}'
    checks = check_settings(request, response)
    assert checks['mismatches'] == [] and _failure(request, response, checks) is None
    assert len(response['provider_requests']) == 1
    payload = json.dumps(response['provider_requests'])
    assert str(Path.cwd()) not in payload and '<cwd>' not in payload
    assert 'unused' not in payload and 'Authorization' not in payload
