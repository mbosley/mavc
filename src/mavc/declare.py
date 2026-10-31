"""A declaration is configuration, not a flow compiler."""
from __future__ import annotations
from typing import Iterable
import hashlib
from pathlib import Path
from typing import Literal
from pydantic import Field

from .record import Model, digest, read_json


class RetrySettings(Model):
    max_attempts: int = Field(default=1, ge=1, strict=True)
    initial_interval: float = Field(default=0, ge=0, allow_inf_nan=False)


class Settings(Model):
    """Typed knobs, adapter extensions and an opt-in historical boundary."""
    model: str | None = None
    provider: str | None = None
    reasoning: str | None = None
    timeout_seconds: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    max_output_bytes: int | None = Field(default=None, gt=0, strict=True)
    maximum_attempts: int = Field(default=1, ge=1, strict=True)
    retry_on: Literal['pre_call', 'tool_failure'] = 'pre_call'
    max_concurrency: int = Field(default=1, ge=1, strict=True)
    cutoff: float | None = Field(default=None, ge=0, le=1)
    batch_size: int | None = Field(default=None, ge=1, strict=True)
    retry: RetrySettings = RetrySettings()
    pi_settings: dict = {}
    pi_retry: dict = {}
    settings: dict = {}
    request: dict = {}
    legacy: dict = {}
    tool_manifest: list[dict] = []
    submission_corrections: int = Field(default=2, ge=2, le=2)
    terminal_continuation: dict = {}
    tool_routing: str | None = None
    unavailable: str | None = None
    prompt_budget_chars: int | None = Field(default=None, gt=0, strict=True)  # see mavc.model.check_prompt_budget
    prompt_budget_reason: str | None = None


class Declaration(Model):
    flow_id: str
    nodes: dict[str, dict] = {}
    positions: dict[str, Settings] = {}
    context: dict = {}
    execution: dict = {}

    def for_node(self, kind: str) -> dict:
        return {'flow_id': self.flow_id, 'node': self.nodes.get(kind, {}),
                'context': self.context, 'execution': self.execution}

    def for_position(self, position: str) -> dict:
        settings = self.positions.get(position, Settings()).model_dump(exclude_unset=True)
        historical = 'legacy' in settings
        legacy = settings.pop('legacy', {})
        if set(legacy) & set(settings):
            raise ValueError('Legacy settings cannot override typed knobs')
        settings = {**legacy, **settings}
        if settings.get('tool_manifest'):
            from .tools import TERMINAL_CONTINUATION
            settings.setdefault('terminal_continuation', TERMINAL_CONTINUATION)
        if settings.get('tool_routing') is not None:
            raise ValueError('Tool routing is not implemented')
        if not historical and settings.get('model'):
            settings.setdefault('timeout_seconds', 600.0)
            settings.setdefault('maximum_attempts', settings.get('retry', {}).get('max_attempts', 1))
            settings['settings'] = {'tools': [], 'compaction': False, 'cache_warming': 'off',
                                    'transport': 'sse', **settings.get('settings', {})}
            settings['pi_retry'] = {'agent_enabled': False, 'provider_max_retries': 0, **settings.get('pi_retry', {})}
        call_attempts(settings)
        return {**self.for_node(''), 'node': settings, 'position': position}

    def snapshot(self) -> dict:
        value = self.model_dump(exclude={'positions'})
        if self.positions:
            value['positions'] = {k: v.model_dump(exclude_unset=True) if 'legacy' in v.model_fields_set
                                  else self.for_position(k)['node'] for k, v in self.positions.items()}
        return value

    @classmethod
    def load(cls, path: str | Path) -> Declaration:
        return cls.model_validate(read_json(path))


def source_identity(files: Iterable[str | Path], root: str | Path) -> dict[str, str]:
    """Relative filenames and content hashes, usable after a repository move."""
    root = Path(root).resolve()
    return {str(Path(p).resolve().relative_to(root)): hashlib.sha256(Path(p).read_bytes()).hexdigest()
            for p in sorted(map(Path, files))}


def call_attempts(node: dict) -> int:
    """One retry owner. Internal Pi retries consume the call budget themselves."""
    count = node.get('maximum_attempts', node.get('retry', {}).get('max_attempts', 1))
    if not isinstance(count, int) or isinstance(count, bool) or count < 1:
        raise ValueError('maximum attempts must be a positive integer')
    pi = node.get('pi_settings', {}).get('retry', {})
    legacy = node.get('pi_retry', {})
    internal = (bool(node.get('provider_retry')) or pi.get('enabled', False) and pi.get('maxRetries', 0) > 0
                or pi.get('provider', {}).get('maxRetries', 0) > 0
                or legacy.get('agent_enabled', False)
                or legacy.get('provider_max_retries', 0) > 0)
    if node.get('retry_on') == 'tool_failure' and count != 2:
        raise ValueError('Tool-failure recovery requires exactly one rerun')
    if internal and count > 1 and node.get('retry_on') != 'tool_failure':
        raise ValueError('Pi internal retries require one outer call attempt')
    return count
