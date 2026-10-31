"""Small shared helpers for structured-output CLI callers."""
import json
import re
import subprocess


def run(argv: list[str], *, input: str | None = None, cwd=None, timeout: float = 600):
    try:
        return subprocess.run(argv, input=input, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        raise RuntimeError(f'Install {argv[0]} and sign in with its normal CLI before using this adapter') from None
    except subprocess.TimeoutExpired:
        raise RuntimeError(f'{argv[0]} exceeded the declared timeout; completion is unknown') from None


def version(executable: str) -> str:
    result = run([executable, '--version'], timeout=15)
    match = re.search(r'\d+\.\d+\.\d+', result.stdout)
    if result.returncode or not match:
        raise RuntimeError(f'Could not determine {executable} version')
    return match.group()


def prepare(request: dict, provider: str, executable: str) -> tuple[dict, dict, str]:
    node = request['node']
    if node['provider'] != provider:
        raise ValueError(f'This adapter requires provider={provider}')
    if request.get('tool_manifest') or request.get('terminal_tool') or node.get('tool_manifest') or node.get('settings', {}).get('tools'):
        raise ValueError('This CLI adapter does not host MAVC Python tools; use Pi or a separate MCP adapter')
    if node.get('harness_settings') or node.get('max_output_bytes') is not None:
        raise ValueError('This CLI adapter supports model, reasoning, timeout and schema; no harness_settings or byte cap')
    selected = version(executable)
    if request.get('harness_version') and request['harness_version'] != selected:
        raise ValueError(f'Declared CLI version {request["harness_version"]}; installed {selected}')
    return node, json.loads(request['prompt'])['output_schema'], selected


def response(text, node, version, *, model, reasoning, error=None, exit_code=0, **evidence) -> dict:
    return {'text': text, 'stop_reason': 'error' if error or exit_code else 'stop', 'error': error,
            'exit_code': exit_code, **evidence,
            'observed': {'provider': node['provider'], 'model': model, 'reasoning': reasoning,
                         'harness_version': version,
                         'request': {'model': node['model'], 'reasoning': {'effort': node['reasoning']}}}}


def diagnostic(text: str) -> str:
    return re.sub(r'(?i)bearer\s+\S+|\bsk-[A-Za-z0-9_-]{16,}', '[redacted]', text)[-4000:]
