"""Flow projections and honest candidate accounting, independent of transcripts."""
from dataclasses import dataclass, field
from pathlib import Path
import json
from typing import Any


@dataclass(frozen=True)
class Candidate:
    id: str
    status: str
    input: Any = None
    output: Any = None
    metadata: dict = field(default_factory=dict)
    context: dict = field(default_factory=dict)
    failed_tool_call: bool = False
    error: str = ''
    transcript: Any = None


@dataclass(frozen=True)
class Example:
    input: Any
    target: Any
    metadata: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Decision:
    status: str = 'included'
    reason: str = 'Accepted projected target'

    def __post_init__(self):
        if self.status not in {'included','excluded','quarantined'} or not self.reason:
            raise ValueError('require included/excluded/quarantined with a reason')


@dataclass(frozen=True)
class Admission:
    id: str
    decision: Decision
    metadata: dict
    example: Example | None = None


def collect(folders, *, kinds=None, resolver=None, metadata=None):
    """Stream every selected node, including failures, for a flow to project.

    Completion means execution succeeded; the flow must choose its scientific
    acceptance boundary (e.g. check nodes rather than unchecked calls).
    Core Artifact resolution is imported lazily; a caller may supply a resolver
    returning its value. Domain-specific references stay inside the projection.
    """
    from ..tree import walk_nodes
    if resolver is None:
        from ..record import resolve
        resolver = lambda folder, ref: resolve(folder, ref).value
    for path in folders:
        folder = Path(path)
        run = json.loads((folder/'record.json').read_text())
        for name, node in walk_nodes(run, leaves_only=True):
            if kinds is not None and node['kind'] not in kinds:
                continue
            meta = {**run.get('metadata', {}), 'source_run_path':str(folder),
                    'node':name, 'kind':node['kind']}
            if metadata is not None:
                meta.update(metadata(node, run, folder))
            status = node['outcome']['status']
            reason = node['outcome'].get('reason', '')
            context = {'node':node,'run':run,'folder':folder}
            try:
                inp = resolver(folder, node['input'])
                out = resolver(folder, node['output']) if node.get('output') else None
            except (ValueError, KeyError, OSError) as exc:
                yield Candidate(f'{folder}#{name}', status, metadata=meta, context=context,
                                error=f'Cannot resolve retained node values: {exc}')
                continue
            yield Candidate(f'{folder}#{name}', status, inp, out, meta, context,
                            bool(meta.get('failed_tool_call')), reason if status != 'completed' else '',
                            meta.get('transcript'))


def admit(candidates, projection, *, policy=lambda example: Decision(),
          test_splits=('test','country_test','benchmark')):
    """Exactly one decision per candidate; failed calls never become targets.

    A projection returns Example or Decision(excluded/quarantined). It may use
    the retained transcript, but an accepted final result does not require one.
    """
    for candidate in candidates:
        meta = dict(candidate.metadata)
        example = None
        if candidate.status != 'completed':
            decision = Decision('excluded',candidate.error or f'Node outcome: {candidate.status}')
        elif candidate.failed_tool_call:
            decision = Decision('excluded','Failed tool call cannot be a positive target')
        elif candidate.error:
            # Completed outcomes with a reason are not resolution errors.
            decision = Decision('quarantined',candidate.error)
        else:
            try:
                result = projection(candidate)
            except (ValueError, KeyError) as exc:
                result = Decision('quarantined',f'Projection could not recover target: {exc}')
            if isinstance(result, Decision):
                if result.status == 'included':
                    raise ValueError('included projection requires an Example')
                decision = result
            elif isinstance(result, Example):
                example = result
                meta.update(example.metadata)
                if candidate.metadata.get('label_source') in {'human_review', 'model_review', 'review'}:
                    meta['label_source'] = candidate.metadata['label_source']
                if candidate.metadata.get('test_only'):
                    meta['test_only'] = True
                example = Example(example.input,example.target,meta)
                if (meta.get('test_only') or meta.get('label_source') in {'human_review', 'model_review', 'review'}) and meta.get('split') not in test_splits:
                    decision = Decision('quarantined','Review labels are test-only')
                else:
                    decision = policy(example)
                    if not isinstance(decision, Decision):
                        raise TypeError('policy must return Decision')
            else:
                raise TypeError('projection must return Example or Decision')
        yield Admission(candidate.id, decision, meta,
                        example if decision.status == 'included' else None)
