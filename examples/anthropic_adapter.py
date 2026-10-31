"""Opt-in Messages API adapter for flat JSON outputs; no SDK dependency or import-time calls."""
import json
import os
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen


def call(request: dict[str, Any]) -> dict[str, Any]:
    """One non-streaming call, no retries or tools. Tests replace urlopen with a mock."""
    node = request['node']
    settings = node.get('settings', {})
    if node['provider'] != 'anthropic' or node['reasoning'] not in ('low', 'medium', 'high', 'xhigh', 'max'):
        raise ValueError('Use provider=anthropic and a supported reasoning effort such as low')
    if settings.get('transport') != 'json' or settings.get('tools') or node.get('tool_manifest') or node.get('pi_settings'):
        raise ValueError('This example requires settings.transport=json, no tools and no pi_settings')
    if 'temperature' in settings:
        raise ValueError('This example does not support temperature')
    schema = json.loads(request['prompt'])['output_schema']
    if schema.get('type') != 'object' or '$defs' in schema:
        raise ValueError('This example supports flat object output schemas, as in the tutorial')
    payload = {'model': node['model'], 'max_tokens': settings.get('max_output_tokens', 512),
               'messages': [{'role': 'user', 'content': request['prompt']}],
               'system': request['system_prompt'], 'stream': False,
               'output_config': {'effort': node['reasoning'],
                                 'format': {'type': 'json_schema', 'schema': {**schema, 'additionalProperties': False}}}}
    key = os.environ.get('ANTHROPIC_API_KEY')
    if not key:
        raise ValueError('Set ANTHROPIC_API_KEY in your environment before calling this adapter')
    headers = {'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json',
               'anthropic-version': '2026-10-21'}
    http = Request('https://api.anthropic.com/v1/messages', data=json.dumps(payload).encode(), headers=headers)
    try:
        with urlopen(http, timeout=node.get('timeout_seconds', 60)) as response:
            status = response.status
            body = json.load(response)
    except HTTPError as exc:
        raise RuntimeError(f'Anthropic HTTP {exc.code}; check credentials, model and request settings') from None
    completed = body.get('stop_reason') == 'end_turn' and not body.get('stop_details')
    # A normalized observation view; the exact wire JSON is retained separately below.
    observed_request = {'model': payload['model'], 'reasoning': {'effort': payload['output_config']['effort']},
                        'max_output_tokens': payload['max_tokens'], 'transport': 'json', 'tools': []}
    return {'text': ''.join(block['text'] for block in body['content'] if block['type'] == 'text'),
            'stop_reason': 'stop' if completed else body.get('stop_reason'),
            'error': None if completed else f"Provider completion not accepted: {body.get('stop_reason')}",
            'provider_request': payload, 'provider_response': body,
            'upstream_response': {'model': body['model'], 'status': 'completed' if completed else 'incomplete'},
            'observed': {'provider': 'anthropic', 'model': body['model'], 'reasoning': node['reasoning'],
                         'active_tools': [], 'request': observed_request, 'response': {'status': status},
                         'resolved_settings': {'transport': 'json', 'compaction': {'enabled': False},
                                               'cache_warming': 'off', 'retry': {'enabled': False},
                                               'provider_retry': {'maxRetries': 0}}}}
