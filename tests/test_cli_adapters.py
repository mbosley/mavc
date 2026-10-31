"""CLI callers are mocked at the subprocess boundary; no live authentication or model calls."""
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest
from pydantic import BaseModel, ConfigDict
from mavc import Runner
from mavc.adapters import _cli, codex, claude_code
from mavc.model import check_settings
from mavc.record import show


class Answer(BaseModel):
    model_config = ConfigDict(extra='forbid')
    answer: str


def request(provider):
    return {'node': {'provider': provider, 'model': 'example-model', 'reasoning': 'high'},
            'system_prompt': 'Return an answer.',
            'prompt': json.dumps({'input': {'text': 'Generic input'}, 'output_schema': Answer.model_json_schema()})}


def fake_cli(monkeypatch, adapter, *, mode='success'):
    calls = []
    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        if argv[-1] == '--version':
            return SimpleNamespace(returncode=0, stdout='codex-cli 0.164.0' if adapter is codex else '2.1.310 (Claude Code)', stderr='')
        assert 'Generic input' in kwargs['input']
        assert Path(kwargs['cwd']).name.startswith('mavc-')
        if mode == 'timeout':
            raise subprocess.TimeoutExpired(argv, kwargs['timeout'])
        if adapter is codex:
            assert json.loads(Path(argv[argv.index('--output-schema')+1]).read_text()) == Answer.model_json_schema()
            assert '--ignore-user-config' in argv and '--sandbox' in argv
            if mode != 'missing':
                Path(argv[argv.index('-o')+1]).write_text('{"answer":"example"}')
            header = 'model: example-model\nreasoning effort: high\nprovider: openai\n'
            return SimpleNamespace(returncode=1 if mode == 'failed' else 0, stdout='',
                                   stderr='' if mode == 'identity' else header)
        assert '--safe-mode' in argv and '--bare' not in argv
        assert argv[argv.index('--tools')+1] == '' and '--no-session-persistence' in argv
        assert json.loads(argv[argv.index('--json-schema')+1]) == Answer.model_json_schema()
        body = {'type': 'result', 'subtype': 'success', 'is_error': False,
                'structured_output': {'answer': 'example'}, 'modelUsage': {'example-model': {}}}
        if mode == 'missing':
            del body['structured_output']
        if mode == 'identity':
            del body['modelUsage']
        if mode == 'failed':
            body.update(subtype='error_max_turns', is_error=True)
        return SimpleNamespace(returncode=0, stdout=json.dumps(body), stderr='')
    monkeypatch.setattr(_cli.subprocess, 'run', run)
    return calls


@pytest.mark.parametrize('adapter,provider,version', [(codex, 'codex', '0.164.0'), (claude_code, 'claude-code', '2.1.310')])
def test_cli_structured_output_and_observation_scope(monkeypatch, adapter, provider, version):
    calls = fake_cli(monkeypatch, adapter)
    req = request(provider)
    result = adapter.call(req)
    assert json.loads(result['text']) == {'answer': 'example'}
    assert result['observed']['harness_version'] == version
    assert 'unobserved' in result['observation_scope']
    assert check_settings(req, result)['mismatches'] == []
    assert len(calls) == 2 and calls[1][1]['timeout'] == 600


@pytest.mark.parametrize('adapter,provider', [(codex, 'codex'), (claude_code, 'claude-code')])
@pytest.mark.parametrize('mode', ['missing', 'identity', 'failed', 'timeout'])
def test_cli_failure_or_missing_output_is_not_accepted(monkeypatch, adapter, provider, mode):
    fake_cli(monkeypatch, adapter, mode=mode)
    if mode == 'timeout':
        with pytest.raises(RuntimeError, match='completion is unknown'):
            adapter.call(request(provider))
    else:
        result = adapter.call(request(provider))
        assert result['stop_reason'] == 'error' and (result['error'] or result['exit_code'])


@pytest.mark.parametrize('adapter,provider', [(codex, 'codex'), (claude_code, 'claude-code')])
def test_cli_tools_are_refused_before_starting_processes(monkeypatch, adapter, provider):
    calls = fake_cli(monkeypatch, adapter)
    req = request(provider)
    req['tool_manifest'] = [{'name': 'search'}]
    with pytest.raises(ValueError, match='does not host MAVC Python tools'):
        adapter.call(req)
    assert calls == []


@pytest.mark.parametrize('base,adapter,provider', [(codex.Codex, codex, 'codex'), (claude_code.ClaudeCode, claude_code, 'claude-code')])
def test_cli_adapters_share_typed_operation_and_record_contract(tmp_path, monkeypatch, base, adapter, provider):
    fake_cli(monkeypatch, adapter)
    class Step(base):
        """Return an answer."""
        class Input(BaseModel):
            text: str
        Output = Answer
    flow = Step()
    with Runner(tmp_path, {'flow_id': 'mock-cli', 'positions': {'Step': {
            'provider': provider, 'model': 'example-model', 'reasoning': 'high'}}}, flow=flow) as runner:
        runner.finish(flow(runner, {'text': 'Generic input'}).model_dump(mode='json'))
    record = show(tmp_path, 'Step')
    assert record['output'] == {'answer': 'example'}
    raw = record['evidence'][0]['value']
    assert raw['settings_check']['mismatches'] == []
    assert 'provider enforcement unobserved' in raw['response']['observation_scope']
