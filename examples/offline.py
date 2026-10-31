"""A recorded model response: no network, credentials or provider client."""
import json
from pathlib import Path
import sys

from pydantic import BaseModel
from mavc import Operation, Runner, verify


class CountWords(Operation):
    """Count whitespace-separated words in the supplied text."""
    class Input(BaseModel):
        text: str

    class Output(BaseModel):
        words: int


def recorded_model(request):
    """A stub adapter with explicit observations, just like a live adapter."""
    node = request['node']
    text = json.loads(request['prompt'])['input']['text']
    payload = {'model': node['model'], 'reasoning': {'effort': node['reasoning']}}
    return {
        'text': json.dumps({'words': len(text.split())}),
        'stop_reason': 'stop',
        'actual_requests': [payload],
        'upstream_response': {'model': node['model'], 'status': 'completed'},
        'observed': {
            'provider': node['provider'], 'model': node['model'],
            'reasoning': node['reasoning'], 'request': payload,
            'active_tools': [],
        },
    }


def main():
    folder = Path(sys.argv[1]) if len(sys.argv) > 1 else Path('runs/offline')
    declaration = {'flow_id': 'word-count/v1', 'positions': {
        'CountWords': {'provider': 'stub', 'model': 'recorded-word-counter', 'reasoning': 'none'},
    }}
    flow = CountWords(caller=recorded_model)
    with Runner(folder, declaration, flow=flow) as runner:
        answer = flow(runner, {'text': 'Flows leave inspectable records.'})
        runner.finish(answer.model_dump())
    assert verify(folder)['completed']
    print(json.dumps(answer.model_dump()))


if __name__ == '__main__':
    main()
