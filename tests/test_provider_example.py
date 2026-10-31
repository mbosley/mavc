"""Mock the example's HTTP boundary: no paid or live requests."""
import importlib.util
import io
import json
from pathlib import Path
from urllib.error import HTTPError

import pytest
from mavc import Operation, Runner
from mavc.record import show
from pydantic import BaseModel

spec = importlib.util.spec_from_file_location('anthropic_example', Path(__file__).parents[1]/'examples/anthropic_adapter.py')
adapter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(adapter)


class Classify(Operation):
    """Label the input and preserve its ID."""
    class Input(BaseModel):
        id: str
        text: str
    class Output(BaseModel):
        id: str
        label: str


def test_adapter_wire_request_and_retained_observations_are_key_free(tmp_path, monkeypatch):
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'unused')
    seen = []
    def mock_http(request, timeout):
        payload = json.loads(request.data)
        assert request.get_header('Authorization') == 'Bearer unused'
        assert timeout == 20 and payload['stream'] is False
        assert payload['output_config']['effort'] == 'low'
        assert payload['output_config']['format']['schema']['additionalProperties'] is False
        seen.append(payload)
        response = io.BytesIO(json.dumps({'model': 'example-model', 'stop_reason': 'end_turn',
            'content': [{'type': 'text', 'text': '{"id":"one","label":"question"}'}]}).encode())
        response.status = 200
        return response
    monkeypatch.setattr(adapter, 'urlopen', mock_http)
    settings = {'provider': 'anthropic', 'model': 'example-model', 'reasoning': 'low', 'timeout_seconds': 20,
                'settings': {'transport': 'json', 'max_output_tokens': 512}}
    flow = Classify(caller=adapter.call)
    with Runner(tmp_path, {'flow_id': 'mock-http', 'positions': {'Classify': settings}}, flow=flow) as runner:
        runner.finish(flow(runner, {'id': 'one', 'text': 'A question?'}).model_dump())
    evidence = show(tmp_path, 'Classify')['evidence'][0]['value']
    assert evidence['response']['provider_request'] == seen[0]
    assert evidence['settings_check']['mismatches'] == []
    assert 'unused' not in json.dumps(evidence)
    assert 'Authorization' not in json.dumps(evidence)


@pytest.mark.parametrize('mode', ['missing-key', 'http-error', 'truncated'])
def test_adapter_failures_do_not_return_accepted_completion(monkeypatch, mode):
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'unused')
    def mock_http(request, timeout):
        if mode == 'missing-key':
            pytest.fail('No HTTP request is allowed without a key')
        if mode == 'http-error':
            raise HTTPError(request.full_url, 401, 'Unauthorized', None, None)
        response = io.BytesIO(json.dumps({'model': 'example-model', 'stop_reason': 'max_tokens',
                                          'content': [{'type': 'text', 'text': '{'}]}).encode())
        response.status = 200
        return response
    monkeypatch.setattr(adapter, 'urlopen', mock_http)
    if mode == 'missing-key':
        monkeypatch.delenv('ANTHROPIC_API_KEY')
    request = {'node': {'provider': 'anthropic', 'model': 'example-model', 'reasoning': 'low',
                        'settings': {'transport': 'json'}},
               'system_prompt': 'Classify.', 'prompt': '{"output_schema":{"type":"object"},"input":{}}'}
    if mode == 'truncated':
        result = adapter.call(request)
        assert result['error'] and result['upstream_response']['status'] == 'incomplete'
    else:
        with pytest.raises((ValueError, RuntimeError)) as error:
            adapter.call(request)
        assert 'unused' not in str(error.value)
