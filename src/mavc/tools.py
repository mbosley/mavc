"""Typed callable tools inside one existing model invocation; no execution graph."""
from dataclasses import dataclass
from copy import deepcopy
from functools import cached_property
import json
import time

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from .fingerprint import fingerprint
from .record import Artifact, digest, now

TERMINAL_CONTINUATION = {'maximum_followups': 2,
    'template': '{error_class}: {error_message}\nCorrect it and call {terminal_tool} again; you have {remaining} corrections left. Correcting and resubmitting in this session is permitted.'}

class ToolInput(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class ToolRejection(Exception):
    def __init__(self, error):
        self.error = error
        super().__init__(str(error))


@dataclass(frozen=True)
class Tool:
    name: str
    Input: type[BaseModel] | None
    call: object = None
    description: str = ''
    terminal: bool = False
    active: bool = True
    schema: dict | None = None
    sources: tuple = ()
    legacy_submit: str | None = None
    checker: dict | None = None

    @cached_property
    def manifest(self):
        schema = self.Input.model_json_schema() if self.Input else self.schema
        if schema is None:
            raise ValueError('A tool requires a declared input schema')
        return {'name': self.name, 'description': self.description, 'input_schema': schema,
            'origin': 'python' if self.Input else 'legacy_js', 'terminal': self.terminal,
            'active': self.active, 'legacy_submit': self.legacy_submit,
            'checker': self.checker,
            'identity': {'schema': digest(schema), 'source': fingerprint(self.call, self.sources)}}

    def describe(self):
        return self.manifest

    def validate(self, value):
        if self.Input:
            return self.Input.model_validate(value)
        import jsonschema
        jsonschema.validate(value, self.schema)
        return value


class ToolSession:
    def __init__(self, tools, context, *, record, corrections=2, deadline=None, cancelled=lambda: False,
                 record_session=lambda row: None, continuation=None):
        self.tools = {tool.name: tool for tool in tools}
        if len(self.tools) != len(tools) or corrections != 2 or (
                continuation and not 0 <= continuation['maximum_followups'] <= corrections):
            raise ValueError('Duplicate tools or unsupported submission/continuation allowance')
        self.context, self.record = context, record
        self.deadline, self.cancelled = deadline, cancelled
        self.calls, self.submissions, self.closed = {}, 0, False
        self.followups, self.followup_limit, self.record_session = 0, None, record_session
        self.continuation = continuation

    def stopped(self, frame):
        reply = {'followup': None, 'closed': self.closed, 'fatal': False, 'error': None,
            'terminal': False, 'accepted': False, 'result': None}
        if self.closed or not self.continuation:
            return reply
        try:
            self.check_live()
            if frame['stop_reason'] != 'stop':
                return reply
            last = next((r for r in reversed(self.calls.values()) if r.get('correction_count') is not None), None)
            if last is None or not last['error']:
                return reply
            error = last['error']
            remaining = 3 - self.submissions
            if self.followup_limit is None:
                self.followup_limit = min(remaining, self.continuation['maximum_followups'])
            if remaining > 0 and self.followups < self.followup_limit:
                terminal = next(t.name for t in self.tools.values() if t.terminal and t.active)
                reply['followup'] = self.continuation['template'].format(error_class=error['class'],
                    error_message=error['message'], terminal_tool=terminal, remaining=remaining)
                self.followups += 1
            else:
                self.closed = reply['closed'] = True
            reply['error'] = error
        except Exception as exc:
            self.closed = reply['closed'] = reply['fatal'] = True
            reply['error'] = {'class': type(exc).__name__, 'message': str(exc)}
        self.record_session({'type': 'terminal_continuation', 'at': now(), 'submissions': self.submissions,
            'followups': self.followups, 'followup_limit': self.followup_limit, **reply})
        return reply

    def check_live(self):
        if self.closed or self.cancelled() or self.deadline is not None and time.monotonic() >= self.deadline:
            raise TimeoutError('Tool invocation closed/cancelled or deadline exceeded')

    def validate_input(self, tool, raw):
        from jsonschema import ValidationError as SchemaError
        guard = self.context.get('format_correction') if tool.terminal else None
        try:
            parsed = tool.validate(raw)
        except (ValidationError, SchemaError) as exc:
            if guard:
                guard.input_rejected(tool.name, raw, exc)
            raise
        if guard:
            guard.restrict(raw, tool.name)
        return parsed

    def dispatch(self, frame):
        from jsonschema import ValidationError as SchemaError
        if 'deadline_monotonic' in frame:
            self.deadline = frame['deadline_monotonic']
        if 'cancelled' in frame:
            self.cancelled = frame['cancelled']
        if frame.get('type') == 'mavc_session_stopped':
            return self.stopped(frame)
        identifier, name, phase = frame['id'], frame['name'], frame['phase']
        tool = self.tools.get(name)
        if tool is None:
            raise ValueError('Undeclared tool: ' + name)
        row = self.calls.get(identifier)
        if row is None:
            self.check_live()
            row = {'id': identifier, 'parent_id': frame.get('parent_id'), 'name': name,
                'input': deepcopy(frame['input']), 'origin': 'python' if tool.Input else 'legacy_js',
                'terminal': tool.terminal, 'outcome': 'pending', 'at': now()}
            self.calls[identifier] = row
            self.record(row.copy())
        if row['name'] != name or row['input'] != frame['input'] or row['parent_id'] != frame.get('parent_id'):
            raise ValueError('Tool ID/input changed between phases')
        if phase in ('observe_result', 'sdk_rejected') and row['outcome'] == 'fatal':
            row['execution_error_result'] = deepcopy(frame.get('result', frame.get('sdk_error')))
            self.record(row.copy())
            return row['reply']  # Preserve the causal refusal when the harness reports its induced abort.
        if phase == 'observe_result' and frame.get('isError'):
            details = frame['result'].get('details', {})
            decision = row.get('reply') or next((r.get('reply') for r in reversed(self.calls.values())
                if r['parent_id'] == identifier and r.get('reply')), None)
            rejection = decision and not decision['accepted'] and details.get('accepted') is False and details.get('error') == decision['error']
            draft_rejection = tool.call is None and not tool.terminal and details.get('accepted') is False
            if not (rejection or draft_rejection):
                self.closed = True
                error = {'class': 'ToolExecutionError', 'message': ''.join(
                    item.get('text', '') for item in frame['result'].get('content', []))}
                reply = {'accepted': False, 'result': None, 'error': error, 'terminal': tool.terminal,
                    'correction_count': None, 'closed': True, 'fatal': True}
                row.update(outcome='fatal', result=None, error=error, correction_count=None,
                    execution_error_result=deepcopy(frame['result']), reply=reply)
                self.record(row.copy())
                return reply
        if row['outcome'] != 'pending':
            if phase == 'observe_result':
                return row['reply']
            raise ValueError('Tool invocation already settled')
        if phase == 'observe_result':
            row.update(outcome='observed', result=frame['result'])
            reply = {'accepted': frame['result'].get('details', {}).get('accepted'),
                'result': frame['result'], 'error': None, 'terminal': False,
                'correction_count': None, 'closed': self.closed, 'fatal': False}
        else:
            error, result, fatal = None, None, False
            try:
                self.check_live()
                guard = self.context.get('format_correction')
                if guard:
                    guard.before_call(tool)
                if phase == 'begin':
                    if guard and tool.terminal and tool.call is None:
                        try:
                            tool.validate(frame['input'])
                        except (ValidationError, SchemaError):
                            pass  # SDK rejection records and counts the invalid input once.
                        else:
                            guard.restrict(frame['input'], tool.name)
                    return {'accepted': None, 'closed': self.closed, 'fatal': False}
                parsed = self.validate_input(tool, frame['input'])
                if phase == 'sdk_rejected':
                    message = frame.get('sdk_error', {}).get('message') or ''.join(
                        item.get('text', '') for item in frame.get('result', {}).get('content', []))
                    raise RuntimeError('Python-valid input rejected by the harness: ' + message)
                if phase != 'invoke' or tool.call is None:
                    raise RuntimeError('Invalid tool dispatch phase/binding')
                result = tool.call(self, parsed)
                json.dumps(result, allow_nan=False)
                self.check_live()
            except (ValidationError, SchemaError, ToolRejection) as exc:
                exc = exc.error if isinstance(exc, ToolRejection) else exc
                error = {'class': type(exc).__name__, 'message': str(exc)}
            except Exception as exc:
                error, fatal = {'class': type(exc).__name__, 'message': str(exc)}, True
            correction = None
            if tool.terminal and not fatal:
                correction = self.submissions
                self.submissions += 1
                self.closed = error is None or self.submissions == 3
            self.closed = self.closed or fatal
            reply = {'accepted': error is None, 'result': result if error is None else None,
                'error': error, 'terminal': tool.terminal, 'correction_count': correction,
                'closed': self.closed, 'fatal': fatal}
            row.update(outcome='fatal' if fatal else 'rejected' if error else 'accepted',
                result=reply['result'], error=error, correction_count=correction)
        row['reply'] = reply
        self.record(row.copy())
        return reply


class ReadSource(ToolInput):
    source_unit_id: str
    start: int = Field(default=0, ge=0)
    count: int = Field(default=12000, ge=1, le=12000)


def read_source(session, input):
    source = session.context['source'](input.source_unit_id)
    end = min(len(source['text']), input.start + input.count)
    return {'source_unit_id': input.source_unit_id, 'locator': source['locator'],
        **({'source_unit_disposition': source['source_unit_disposition']}
            if 'source_unit_disposition' in source else {}),
        'start': input.start, 'text': source['text'][input.start:end],
        'next_start': end if end < len(source['text']) else None}


class Submit(ToolInput):
    proposal: dict


def submit(session, input):
    session.context['compile'](input.proposal)
    session.check_live()
    return input.proposal


def record_tools(children, row):
    with children.runner.lock:
        attempt = children.runner.index()[children.node].attempts
        row = {**row, 'attempt': attempt}
        rows = children.details.setdefault('tool_calls', {})
        key = json.dumps([attempt, row['id']]) if children.settings.get('maximum_attempts', 1) > 1 else row['id']
        rows[key] = row
        children.runner.save()
    if row['outcome'] != 'pending':
        children.evidence(children.runner.retain(Artifact.create('model-tool-call/v1', row)).ref())


def record_session(children, row):
    with children.runner.lock:
        children.details.setdefault('terminal_continuations', []).append(row)
        children.runner.save()
    children.evidence(children.runner.retain(Artifact.create('model-session-event/v1', row)).ref())
