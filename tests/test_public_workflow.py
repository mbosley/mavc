"""Standalone onboarding errors and resolved access to model evidence."""
import json
import subprocess
import sys

import pytest
from pydantic import BaseModel
from mavc import Operation, Runner, sequence, for_each
from mavc.record import show
from mavc.runner import NewRunRequired
from mavc.stub import classify


class Classify(Operation):
    """Classify the supplied text and keep its ID."""
    class Input(BaseModel):
        id: str
        text: str
    class Output(BaseModel):
        id: str
        label: str


def check(children, rows):
    return {'counts': {'questions': sum(row['label'] == 'question' for row in rows)}}


def workflow():
    return sequence('Review', [for_each('LabelAll', Classify(caller=classify), key=lambda row: row['id']),
                               Operation('Check', run=check)])


def config(position='Review.LabelAll.Classify'):
    return {'flow_id': 'onboarding', 'positions': {
        position: {'provider': 'stub', 'model': 'punctuation', 'reasoning': 'none'}}}


def test_wrong_position_error_names_expected_and_declared_paths(tmp_path):
    with Runner(tmp_path, config('Classify')) as runner:
        with pytest.raises(RuntimeError) as error:
            workflow()(runner, [{'id': 'one', 'text': 'A question?'}])
        message = str(error.value)
        assert "Set positions['Review.LabelAll.Classify']" in message
        assert 'Declared positions: Classify' in message
        assert 'missing: provider, model, reasoning' in message


def test_show_resolves_nested_result_model_evidence_and_changed_input(tmp_path):
    data = [{'id': 'one', 'text': 'A question?'}]
    flow = workflow()
    with Runner(tmp_path, config()) as runner:
        runner.finish(flow(runner, data))
    assert show(tmp_path)['output'] == {'counts': {'questions': 1}}
    call = show(tmp_path, 'Review/0/one')
    assert call['input'] == data[0]
    assert call['output'] == {'id': 'one', 'label': 'question'}
    evidence = call['evidence'][0]
    assert evidence['type'] == 'model-call/v1'
    assert evidence['value']['response']['observed']['provider'] == 'stub'
    assert evidence['value']['settings_check']['mismatches'] == []
    cli = subprocess.run([sys.executable, '-m', 'mavc', 'show', str(tmp_path), 'Review/0/one'],
                         check=True, capture_output=True, text=True)
    assert json.loads(cli.stdout) == call
    with pytest.raises(ValueError, match='Available nodes: Review'):
        show(tmp_path, 'missing')
    with Runner(tmp_path, config()) as runner:
        with pytest.raises(NewRunRequired, match="Node 'Review': input changed; use a new run directory"):
            flow(runner, [{'id': 'one', 'text': 'Changed.'}])
    assert show(tmp_path)['output'] == {'counts': {'questions': 1}}


def test_changed_settings_names_position(tmp_path):
    with Runner(tmp_path, config()):
        pass
    changed = config()
    changed['positions']['Review.LabelAll.Classify']['model'] = 'changed'
    with pytest.raises(NewRunRequired, match=r'positions.Review.LabelAll.Classify; use a new run directory'):
        Runner(tmp_path, changed)
