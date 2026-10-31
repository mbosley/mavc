"""One claude -p structured-output session; authentication stays with the CLI."""
import json
import os
from pathlib import Path
import tempfile

from ..operation import Operation
from . import _cli


def call(request: dict) -> dict:
    executable = os.environ.get('MAVC_CLAUDE', 'claude')
    node, schema, version = _cli.prepare(request, 'claude-code', executable)
    with tempfile.TemporaryDirectory(prefix='mavc-claude-') as work:
        command = [executable, '-p', '--model', node['model'], '--effort', node['reasoning'],
                   '--output-format', 'json', '--json-schema', json.dumps(schema),
                   '--system-prompt', request['system_prompt'], '--tools', '', '--safe-mode',
                   '--setting-sources', '', '--strict-mcp-config', '--permission-mode', 'dontAsk',
                   '--no-session-persistence']
        result = _cli.run(command, input=request['prompt'], cwd=work, timeout=node.get('timeout_seconds', 600))
    body = json.loads(result.stdout) if result.stdout.strip() else {}
    usage = body.get('modelUsage', {})
    model = next(iter(usage)) if len(usage) == 1 else body.get('model')
    valid = not result.returncode and not body.get('is_error') and body.get('subtype') == 'success' and 'structured_output' in body
    error = None if valid else _cli.diagnostic(result.stderr) or f'Claude Code did not return successful structured_output ({body.get("subtype")})'
    if model is None:
        error = error or 'Claude Code did not report one model in modelUsage; no output accepted'
    return _cli.response(json.dumps(body.get('structured_output')), node, version, model=model,
                         reasoning=node['reasoning'], error=error, exit_code=result.returncode, cli_result=body,
                         observation_scope='Model from CLI result; reasoning is the captured --effort flag, provider enforcement unobserved')


class ClaudeCode(Operation):
    """Subclass like Operation; uses normal Claude Code authentication, including subscriptions."""
    caller = staticmethod(call)
    sources = (Path(_cli.__file__),)
