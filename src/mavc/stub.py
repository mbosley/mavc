"""Offline tutorial classifier; demonstrates wiring, not model quality."""
import json
from typing import Any


def classify(request: dict[str, Any]) -> dict[str, Any]:
    """Label text ending in '?' as a question; return the model observation contract."""
    node = request['node']
    item = json.loads(request['prompt'])['input']
    output = {'id': item['id'], 'label': 'question' if item['text'].rstrip().endswith('?') else 'statement'}
    payload = {'model': node['model'], 'reasoning': {'effort': node['reasoning']}}
    return {'text': json.dumps(output), 'stop_reason': 'stop', 'actual_requests': [payload],
            'upstream_response': {'model': node['model'], 'status': 'completed'},
            'observed': {'provider': node['provider'], 'model': node['model'],
                         'reasoning': node['reasoning'], 'request': payload, 'active_tools': []}}
