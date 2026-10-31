"""Small JSON artifacts and honest, provider-free run records."""
from __future__ import annotations
from typing import Iterable
import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from .tree import walk_nodes

Status = Literal['completed', 'needs_correction', 'needs_human_review', 'rejected', 'failed']


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_json(path: str | Path) -> Any:
    return json.loads(Path(path).read_text())


def write_json(path: str | Path, value: Any) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix='.write-')
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


class Model(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)


class Artifact(Model):
    type: str = Field(min_length=1)
    value: Any
    lineage: list[str] = Field(default_factory=list)
    sha256: str

    @classmethod
    def create(cls, type: str, value: Any, lineage: Iterable[str] = ()) -> Artifact:
        body = {'type': type, 'value': value, 'lineage': list(lineage)}
        return cls(**body, sha256=digest(body))

    @model_validator(mode='after')
    def check_hash(self):
        if self.sha256 != digest(self.model_dump(exclude={'sha256'})):
            raise ValueError('Artifact content hash differs')
        return self

    def ref(self) -> dict[str, str]:
        return {'type': self.type, 'sha256': self.sha256}


class Outcome(Model):
    status: Status
    reason: str = ''


class NodeRecord(Model):
    node: str
    kind: str
    input: dict
    input_sha256: str
    output: dict | None = None
    declaration: dict
    declaration_sha256: str
    implementation: dict[str, str]
    depends_on: list[str] = Field(default_factory=list)
    outcome: Outcome
    attempts: int = 0
    reused: bool = False
    errors: list[dict] = Field(default_factory=list)
    position: str = ''
    purpose: str = ''
    composition: str = ''
    children: dict[str, 'NodeRecord'] = Field(default_factory=dict)
    details: dict = Field(default_factory=dict)


class RunRecord(Model):
    record_type: Literal['mavc-run/v1', 'mavc-run/v2'] = 'mavc-run/v1'
    created_at: str = Field(default_factory=now)
    declaration_sha256: str
    outcome: Outcome = Field(default_factory=lambda: Outcome(status='needs_correction', reason='Run unfinished'))
    nodes: dict[str, NodeRecord] = Field(default_factory=dict)
    outputs: dict | None = None
    metadata: dict = Field(default_factory=dict)
    invocations: list[dict] = Field(default_factory=list)


def complete_children(node):
    required = [node.details.get('selected')] if node.composition == 'route' else list(node.children)
    return all(name in node.children and
        node.children[name].outcome.status == 'completed' for name in required)


def resolve(folder: str | Path, ref: dict[str, str]) -> Artifact:
    artifact = Artifact.model_validate(read_json(Path(folder) / 'outputs/objects' / (ref['sha256'] + '.json')))
    if artifact.ref() != ref:
        raise ValueError('Artifact reference differs')
    return artifact


def show(folder: str | Path, node: str | None = None) -> dict[str, Any]:
    """Read final results or one node's values and evidence without hash navigation."""
    record = read_json(Path(folder) / 'record.json')
    nodes = dict(walk_nodes(record))
    if node is None:
        return {'outcome': record['outcome'],
                'output': resolve(folder, record['outputs']).value if record.get('outputs') else None,
                'nodes': {name: {'position': row.get('position', ''), 'outcome': row['outcome']}
                          for name, row in nodes.items()}}
    if node not in nodes:
        raise ValueError(f'Unknown node {node!r}. Available nodes: {", ".join(nodes) or "(none)"}')
    row = nodes[node]
    return {**{key: row.get(key) for key in ('node', 'position', 'outcome', 'attempts', 'reused', 'errors')},
            'input': resolve(folder, row['input']).value if row.get('input') else None,
            'output': resolve(folder, row['output']).value if row.get('output') else None,
            'evidence': [{'type': ref['type'], 'value': resolve(folder, ref).value}
                         for ref in row.get('details', {}).get('evidence', [])]}


def verify(folder: str | Path) -> dict[str, Any]:
    """Check retained inputs/results; domain checks belong to the calling flow."""
    folder = Path(folder)
    record = RunRecord.model_validate(read_json(folder / 'record.json'))
    declaration = read_json(folder / 'declaration.json')
    from .declare import Declaration
    config = Declaration.model_validate(declaration)
    if digest(declaration) != record.declaration_sha256:
        raise ValueError('Run declaration changed')
    objects = list((folder / 'outputs/objects').glob('*.json'))
    for path in objects:
        artifact = Artifact.model_validate(read_json(path))
        if path.stem != artifact.sha256:
            raise ValueError('Artifact filename differs from its hash')
    entries = list(walk_nodes(record))
    index = dict(entries)
    if len(index) != len(entries):
        raise ValueError('Duplicate node identity in tree')
    for name, node in entries:
        if name != node.node or digest(node.declaration) != node.declaration_sha256:
            raise ValueError('Node declaration differs')
        for key in ('flow_id', 'context', 'execution'):
            if node.declaration[key] != declaration[key]:
                raise ValueError('Node settings differ from run declaration')
        settings = (config.for_position(node.position)['node'] if node.position
                    else declaration['nodes'].get(node.kind, {}))
        if node.declaration['node'] != settings:
            raise ValueError('Node settings differ from declared node')
        if node.position and (node.declaration['position'] != node.position or
                node.declaration['operation']['composition'] != node.composition):
            raise ValueError('Operation position or composition differs')
        if node.declaration['implementation'] != node.implementation:
            raise ValueError('Node implementation differs')
        for ref in node.details.get('evidence', []):
            resolve(folder, ref)
        if digest(resolve(folder, node.input).value) != node.input_sha256:
            raise ValueError('Node input differs')
        if node.outcome.status == 'completed':
            if node.output is None:
                raise ValueError('Completed node missing output')
            output = resolve(folder, node.output)
            if any(dep not in index or index[dep].outcome.status != 'completed'
                   for dep in node.depends_on):
                raise ValueError('Completed node has missing/failed dependency')
            if node.declaration['dependencies'] != {dep: index[dep].output for dep in node.depends_on}:
                raise ValueError('Node dependencies differ')
            if output.lineage != [node.input['sha256'], *[index[dep].output['sha256'] for dep in node.depends_on],
                                  *[n.output['sha256'] for n in node.children.values() if n.output]]:
                raise ValueError('Node output differs from input/dependency/child lineage')
            if node.composition and not complete_children(node):
                raise ValueError('Completed composition has unfinished children or changed lineage')
    if record.outcome.status == 'completed':
        if not record.nodes or record.outputs is None or any(n.outcome.status != 'completed' for n in record.nodes.values()):
            raise ValueError('Completed run has unfinished/missing nodes or output')
        resolve(folder, record.outputs)
    counts = {status: sum(n.outcome.status == status for _, n in entries) for status in
              ['completed', 'needs_correction', 'needs_human_review', 'rejected', 'failed']}
    return {'outcomes': counts, 'verified': True, 'status': record.outcome.status, 'nodes': len(entries),
            'leaves': sum(not n.composition for _, n in entries), 'composites': sum(bool(n.composition) for _, n in entries),
            'artifacts': len(objects), 'completed': record.outcome.status == 'completed',
            'scope': 'Retained JSON consistency; domain verification and semantic review are separate.'}
