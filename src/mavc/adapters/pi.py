"""One public Pi session per model invocation, with optional Python tools."""
from functools import partial
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import tempfile
import time
from typing import Callable

from ..operation import Operation
from ._duplex import exchange

PI_VERSION = '1.4.0'
_BRIDGE = Path(__file__).with_name('pi_bridge.mjs')


def _sanitize(text: str) -> str:
    return re.sub(r'(?i)bearer\s+\S+|\bsk-[A-Za-z0-9_-]{16,}', '[redacted]', text)


def _stop(child, drain=False):
    """Reap only the subprocess group owned by this call."""
    def send(sig):
        try:
            if os.name == 'posix':
                os.killpg(child.pid, sig)
            else:
                child.send_signal(sig)
        except ProcessLookupError:
            pass
    send(signal.SIGTERM)
    try:
        child.wait(timeout=2)
    except subprocess.TimeoutExpired:
        send(signal.SIGKILL)
        child.wait()


def call(request: dict, *, tool_dispatch: Callable | None = None, pi_root: str | Path | None = None) -> dict:
    """Use an isolated npm install; credentials are resolved by Pi from its standard environment."""
    root = pi_root or os.environ.get('MAVC_PI_ROOT')
    if not root:
        raise ValueError('Set MAVC_PI_ROOT to the directory containing node_modules after installing '
                         f'@earendil-works/pi-coding-agent@{PI_VERSION}; see docs/pi.md')
    limit = request['node'].get('timeout_seconds', 600)
    cap = request['node'].get('max_output_bytes')
    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        try:
            child = subprocess.Popen([os.environ.get('MAVC_PI_NODE', 'node'), str(_BRIDGE), str(Path(root).resolve())],
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=err,
                                     start_new_session=os.name == 'posix')
        except FileNotFoundError:
            raise RuntimeError('Pi requires Node >=22.19 on PATH (or MAVC_PI_NODE)') from None
        try:
            stdout, failure = exchange(child, request, tool_dispatch, out, time.monotonic() + limit, cap, _stop)
            err.seek(0)
            diagnostics = _sanitize(err.read().decode(errors='replace'))[-16000:]
            if failure or child.returncode:
                reason = failure or f'Pi exited with status {child.returncode}'
                raise RuntimeError(_sanitize(reason + (': ' + diagnostics.strip() if diagnostics else '')))
            return json.loads(stdout)
        finally:
            if child.poll() is None:
                _stop(child)
            child.stdin.close()
            child.stdout.close()


class Pi(Operation):
    """Subclass like Operation; bind_tools supplies Python tools when declared."""
    sources = (_BRIDGE, Path(__file__).with_name('_duplex.py'))
    caller = staticmethod(call)

    def bind_caller(self, dispatch: Callable) -> Callable:
        return partial(self.caller, tool_dispatch=dispatch)
