"""One codex exec session with a final output schema; no MAVC Python-tool bridge."""
import os
from pathlib import Path
import re
import tempfile
import json

from ..operation import Operation
from . import _cli


def call(request: dict) -> dict:
    executable = os.environ.get('MAVC_CODEX', 'codex')
    node, schema, version = _cli.prepare(request, 'codex', executable)
    with tempfile.TemporaryDirectory(prefix='mavc-codex-') as work:
        schema_path, output = Path(work)/'schema.json', Path(work)/'answer.json'
        schema_path.write_text(json.dumps(schema))
        command = [executable, 'exec', '--model', node['model'], '-c', f'model_reasoning_effort={node["reasoning"]}',
                   '--sandbox', 'read-only', '--ignore-user-config', '--ignore-rules',
                   '--disable', 'hooks', '--disable', 'plugins', '--disable', 'shell_tool', '--disable', 'unified_exec',
                   '-c', 'project_doc_max_bytes=0', '-c', 'web_search="disabled"',
                   '--skip-git-repo-check', '--ephemeral', '-C', work,
                   '--output-schema', str(schema_path), '-o', str(output), '-']
        prompt = request['system_prompt'] + '\n\nAll inputs are supplied below. Do not read files or run commands.\n\n' + request['prompt']
        result = _cli.run(command, input=prompt, cwd=work, timeout=node.get('timeout_seconds', 600))
        text = output.read_text().strip() if output.exists() else ''
    header = dict(re.findall(r'^(model|reasoning effort|provider): (.+)$', result.stderr, re.M))
    error = _cli.diagnostic(result.stderr) if result.returncode else None
    if not text:
        error = error or 'Codex did not write its final schema-constrained output'
    if not all(header.get(key) for key in ('model', 'reasoning effort')):
        error = error or 'Codex did not report model and reasoning in its session header; no output accepted'
    return _cli.response(text, node, version, model=header.get('model'), reasoning=header.get('reasoning effort'),
                         error=error, exit_code=result.returncode, cli_header=header,
                         observation_scope='Model and reasoning from the CLI session header; HTTP payload and provider enforcement unobserved')


class Codex(Operation):
    """Subclass like Operation; uses your installed and signed-in Codex CLI."""
    caller = staticmethod(call)
    sources = (Path(_cli.__file__),)
