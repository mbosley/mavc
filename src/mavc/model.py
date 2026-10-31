"""Typed model leaves at the Operation boundary; science remains caller-owned."""
from copy import deepcopy
import inspect
import json

from .record import Artifact

_MISSING = object()


def _get(value, *path):
    for key in path:
        if not isinstance(value, dict) or key not in value:
            return _MISSING
        value = value[key]
    return value


def _compare(path, expected, actual, result):
    if isinstance(expected, dict):
        for key, value in expected.items():
            _compare(path + '.' + key, value, _get(actual, key), result)
    else:
        status = 'unobserved' if actual is _MISSING else 'checked' if actual == expected else 'mismatches'
        result[status].append(path)


def check_settings(request, response):
    """Check captured fields; missing historical observations remain explicit."""
    result = {key: [] for key in ('checked', 'unobserved', 'mismatches')}
    node, observed = request['node'], response.get('observed', {})
    actual = observed.get('request', response.get('actual_request', {}))
    settings = observed.get('resolved_settings', {})
    tools = observed.get('active_tools', response.get('tools', _MISSING))
    retry = settings.get('retry', {})
    provider_retry = settings.get('provider_retry', {})
    provider = _get(observed, 'provider')
    compaction = _get(settings, 'compaction')
    seen = {'model': _get(observed, 'model'), 'reasoning': _get(observed, 'reasoning'), 'provider': provider,
        'tools': tools, 'pool_transport': _get(actual, 'pool_transport'), 'pi_version': _get(observed, 'pi_version'),
        'settings': {'tools': tools if 'tools' not in node else _MISSING,
            'transport': settings.get('transport', _get(actual, 'transport')),
            'max_output_tokens': _get(actual, 'max_output_tokens'), 'temperature': _get(actual, 'temperature'),
            'compaction': _get(compaction, 'enabled') if isinstance(node.get('settings', {}).get('compaction'), bool) else compaction,
            'cache_warming': _get(settings, 'cache_warming')},
        'pi_retry': {'agent_enabled': _get(retry, 'enabled'), 'provider_max_retries': _get(provider_retry, 'maxRetries')},
        'pi_settings': {'compaction': compaction, 'transport': _get(settings, 'transport'),
            'cacheWarming': _get(settings, 'cache_warming'), 'retry': {**retry, 'provider': provider_retry}}}
    _compare('node', node, seen, result)
    upstream_model = _get(response, 'upstream_response', 'model')
    if upstream_model is not _MISSING and upstream_model is not None:
        _compare('upstream_response.model', node['model'], upstream_model, result)
    else:
        result['unobserved'].append('upstream_response.model')
    if 'pi_version' in request:
        _compare('pi_version', request['pi_version'], _get(observed, 'pi_version'), result)
    captured = response.get('actual_requests')
    payloads = captured or [actual]
    if not captured:
        result['unobserved'].append('actual_requests')
    for i, payload in enumerate(payloads):
        prefix = f'actual_requests[{i}]' if captured else 'observed.request' if 'request' in observed else 'actual_request'
        for key, expected in [('model', node['model']), ('reasoning', {'effort': node['reasoning']})]:
            _compare(f'{prefix}.{key}', expected, _get(payload, key), result)
        for key in ('max_output_tokens', 'temperature'):
            if key in node.get('settings', {}):
                _compare(f'{prefix}.{key}', node['settings'][key], _get(payload, key), result)
        if 'output_format' in request:
            _compare(f'{prefix}.text.format', request['output_format'], _get(payload, 'text', 'format'), result)
        if 'tools' in payload:
            _compare(f'{prefix}.tool_names', sorted(node.get('tools', node.get('settings', {}).get('tools', []))),
                     sorted(t.get('name', t.get('type')) for t in payload['tools']), result)
            for j, tool in enumerate(payload['tools']):
                if tool.get('type') == 'function' and 'ordinary_function_strict' in node.get('pool_transport', {}):
                    _compare(f'{prefix}.tools[{j}].strict', node['pool_transport']['ordinary_function_strict'], _get(tool, 'strict'), result)
    transport = node.get('pi_settings', node.get('settings', {})).get('transport')
    if transport and transport != 'auto':
        _compare('observed.request.transport', transport, _get(actual, 'transport'), result)
    if response.get('actual_requests') and 'tools' in actual:
        _compare('observed.request.tools', actual['tools'], _get(payloads[-1], 'tools'), result)
    if _failure(request, response, result):
        result['mismatches'].append('response.completion')
    return result


def _failure(request, response, checks, *, require_content=True):
    if response.get('error') or response.get('exit_code') or response.get('stop_reason') not in ('stop', 'toolUse'):
        return str(response.get('error') or 'Pi call failed/partial or completion unknown')
    if any(field in checks['unobserved'] for field in ('node.model', 'node.reasoning')) or any(not any(
            field.startswith(('actual_request', 'observed.request')) and field.endswith(suffix)
            for field in checks['checked']) for suffix in ('.model', '.reasoning.effort')):
        return 'Required model/reasoning observations missing'
    if any(field.startswith('actual_requests[') and field.endswith(('.model', '.reasoning.effort')) for field in checks['unobserved']):
        return 'Captured request missing required model/reasoning identity'
    if response.get('stop_reason') == 'toolUse' and not request.get('terminal_tool'):
        return 'Tool use without terminal completion'
    if require_content and request.get('terminal_tool') and response.get('proposal') is None:
        return 'Terminal proposal missing'
    if require_content and not request.get('terminal_tool') and not (isinstance(response.get('text'), str) and response['text'].strip()):
        return 'Empty model content'
    status = _get(response, 'observed', 'response', 'status')
    if status is not _MISSING and not 200 <= status < 300:
        return f'Provider HTTP status {status}'
    if _get(response, 'upstream_response', 'status') not in (_MISSING, None, 'completed'):
        return 'Upstream response incomplete'
    if checks['mismatches']:
        return 'Observed settings differ: ' + ', '.join(checks['mismatches'])


def _caller(operation):
    caller = getattr(operation, 'caller', None)
    if caller is None:
        raise ValueError('Model operations require caller=your_adapter; MAVC does not bundle a provider client')
    return caller


def sources(operation):
    """Renderer/parser files; Operation owns caller identity and explicit boundaries."""
    result = (execute, getattr(operation, 'render', None) or render,
              getattr(operation, 'parse', None) or parse)
    if not getattr(operation, 'parse', None):
        result += (json.loads, _unique_object,)
    return result


def render(operation, input, settings, children):
    """Cleaned docstring plus compact sorted UTF-8 schema/input JSON, no normalization."""
    body = {'input_schema': operation.Input.model_json_schema(),
            'output_schema': operation.Output.model_json_schema(), 'input': input.model_dump(mode='json')}
    return {**settings.get('request', {}), 'node': settings,
        'system_prompt': inspect.getdoc(type(operation)) or '',
        'prompt': json.dumps(body, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False),
        **{key: settings[key] for key in ('pi_settings', 'terminal_tool', 'pi_version', 'route') if key in settings}}


def _unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f'Duplicate JSON key: {key}')
        value[key] = item
    return value


def parse(operation, request, response, input, children):
    if request.get('terminal_tool'):
        return response['proposal']
    return json.loads(response['text'], object_pairs_hook=_unique_object)


# Give model steps a compact index and tools when their inputs grow with the corpus.
# A step that truly needs more declares prompt_budget_chars with a prompt_budget_reason in its flow declaration.
DEFAULT_PROMPT_BUDGET_CHARS = 120_000


def prompt_chars(request):
    """Characters handed to a model, including declared system prompt copies."""
    system = len(request.get('system_prompt') or '')
    copies = request.get('system_prompt_copies', 1)
    return copies * system + len(request.get('prompt') or '')


def check_prompt_budget(size, settings):
    budget = settings.get('prompt_budget_chars') or DEFAULT_PROMPT_BUDGET_CHARS
    if budget != DEFAULT_PROMPT_BUDGET_CHARS and not str(settings.get('prompt_budget_reason') or '').strip():
        raise ValueError('prompt_budget_chars differs from the default without a prompt_budget_reason')
    if size > budget:
        raise ValueError(f'Prompt of {size} characters exceeds this step\'s budget of {budget}: give the step a '
                         'compact index and search/read tools, or declare a '
                         'larger prompt_budget_chars with a prompt_budget_reason')


def execute(operation, children, input):
    """One invocation, one raw evidence artifact, checks before typed output caching."""
    settings = children.settings
    renderer, parser = getattr(operation, 'render', None), getattr(operation, 'parse', None)
    record = {'request': None, 'response': None, 'settings_check': None,
              'renderer': 'custom' if renderer else 'signature'}
    error, session = None, None
    try:
        if settings.get('unavailable'):
            raise ValueError(settings['unavailable'])
        children.runner.declaration.validate_model(children.position)
        request = renderer(input, settings, children) if renderer else render(operation, input, settings, children)
        record['request'] = request
        record['prompt_chars'] = prompt_chars(request)
        check_prompt_budget(record['prompt_chars'], settings)
        controls = ('pi_settings', 'terminal_tool', 'pi_version', 'route')
        declared = {**settings.get('request', {}), **settings}
        if request.get('node') != settings or {k: request[k] for k in controls if k in request} != {
                k: declared[k] for k in controls if k in declared}:
            raise ValueError('Rendered request settings differ from effective position settings')
        caller = _caller(operation)
        if settings.get('tool_manifest'):
            from .tools import ToolSession, record_tools, record_session
            import time
            tools, context = operation.bind_tools(input, request, children)
            if [tool.describe() for tool in tools] != settings['tool_manifest']:
                raise ValueError('Bound tools differ from static manifest')
            session = ToolSession(tools, context, record=lambda row: record_tools(children, row),
                record_session=lambda row: record_session(children, row),
                continuation=settings['terminal_continuation'],
                corrections=settings.get('submission_corrections', 2),
                deadline=time.monotonic() + settings['timeout_seconds'] if settings.get('timeout_seconds') else None)
            request['tool_manifest'] = settings['tool_manifest']
            caller = operation.bind_caller(session.dispatch)
        record['response'] = caller(deepcopy(request))
        record['settings_check'] = check_settings(request, record['response'])
    except Exception as exc:
        error = record['error'] = f'{type(exc).__name__}: {exc}'[-16000:]
    evidence = children.runner.retain(Artifact.create('model-call/v1', record))
    children.evidence(evidence.ref())
    if session is not None and hasattr(operation, 'check_tools'):
        operation.check_tools(session, input, children, record)
    if error:
        raise RuntimeError(error)
    failure = _failure(request, record['response'], record['settings_check'])
    if failure:
        raise ValueError(failure)
    value = parser(request, record['response'], input, children) if parser else parse(operation, request, record['response'], input, children)
    return value  # Operation.invoke validates Output once, before the leaf can be cached.
