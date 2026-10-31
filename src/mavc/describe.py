import argparse
from collections import Counter
import json
import re
from pathlib import Path
from .tree import walk_nodes


def describe(folder: str | Path, *, summary: bool = False, details: bool = False) -> str:
    if Path(folder).is_file():
        declaration = json.loads(Path(folder).read_text())
        from .declare import Declaration
        config = Declaration.model_validate(declaration)
        return '\n'.join(f'{position}:\n' + json.dumps(config.for_position(position)['node'], indent=2)
            for position in config.positions)
    record = json.loads((Path(folder) / 'record.json').read_text())
    def group(nodes):
        for name, node in nodes.items():
            operation = node['declaration'].get('operation', {})
            purpose = ' '.join((node.get('purpose') or operation.get('name', 'Recorded operation')).split())
            route = node.get('details', {}).get('route', {})
            state = {k: v for k, v in node.get('details', {}).items() if k in ('iterations', 'stopped') or k == 'selected' and not route}
            if route:
                state['route'] = {k: route.get(k) for k in ('route', 'model', 'fallback_reason')}
            if operation.get('tools'):
                state['tools'] = operation['tools']
            settings = node['declaration'].get('node', {})
            yield (node.get('position') or name, purpose, node.get('composition', ''), json.dumps(node['outcome'], sort_keys=True),
                   json.dumps(operation.get('signature', {}).get('output', {})),
                   (settings.get('model') or ('unobserved' if 'caller' in operation else None)) if not node.get('composition') else None, settings.get('reasoning'),
                   json.dumps(state, sort_keys=True),
                   tuple(Counter(group(node.get('children', {}))).items()))
    def render(groups, indent='', root=False):
        for (position, purpose, form, outcome, schema, model, reasoning, state, children), count in groups.items():
            if (form == 'for-each' and len(children) == 1 and children[0][0][2] == 'sequence'
                    and json.loads(children[0][0][3])['status'] == 'completed' and children[0][0][7] == '{}'):
                body, times = children[0]
                purpose, children = f'{purpose} ({times}): {body[1]}', body[-1]
            label = 'flow' if root else (form.replace('-', ' ') or ('ask' if model else 'check' if purpose.lower().startswith(('check ', 'verify ')) else 'step'))
            sentence = re.split(r'(?<=[.!?])\s+', purpose, maxsplit=1)[0] if model else purpose
            lines.append(f"{indent}{'' if root else '- '}{label}: {sentence}" + (f' ({count} times)' if count > 1 else ''))
            pad = indent if root else indent + '  '
            if root or json.loads(outcome)['status'] != 'completed':
                lines.append(f"{pad}status: {(record['outcome'] if root else json.loads(outcome))['status']}")
            if model:
                lines.append(f"{pad}model (configured): {model} ({reasoning or 'unspecified'})")
                lines.extend([f'{pad}returns:'] + [f"{pad}  - {field.get('description') or name}" for name, field in json.loads(schema).get('properties', {}).items()])
            lines.extend(f'{pad}{key}: {json.dumps(value, ensure_ascii=False)}' for key, value in json.loads(state).items() if key != 'selected')
            if form:
                lines.append(f"{pad}steps:" if children else f'{pad}steps: []')
                render(dict(children), pad + '  ')
    lines = [f"status: {record['outcome']['status']}", 'steps:'] if len(record['nodes']) != 1 else []
    render(Counter(group(record['nodes'])), indent='  ' if len(record['nodes']) != 1 else '', root=len(record['nodes']) == 1)
    if summary:
        ref = record.get('outputs')
        value = json.loads((Path(folder) / 'outputs/objects' / (ref['sha256'] + '.json')).read_text())['value'] if ref else {}
        lines.extend(['results:'] + [f"  {key}: {len(v) if isinstance(v, list) else json.dumps(v, ensure_ascii=False)}" for key, v in (value if isinstance(value, dict) else {'output items': value}).items() if type(v) in (int, float, list, dict)])
        lines.append(f"  failed steps: {sum(n['outcome']['status'] == 'failed' for _, n in walk_nodes(record, leaves_only=True))}")
    if details:
        lines.append('Recorded details (available_children are templates, not execution counts):\n' + json.dumps(record, indent=2, sort_keys=True))
    return '\n'.join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(prog='mavc')
    commands = parser.add_subparsers(dest='command', required=True)
    outline = commands.add_parser('describe', help='Describe a flow or run')
    outline.add_argument('folder')
    outline.add_argument('--summary', action='store_true')
    outline.add_argument('--details', action='store_true')
    values = commands.add_parser('show', help='Read final results or one node with its evidence as JSON')
    values.add_argument('folder')
    values.add_argument('node', nargs='?')
    args = parser.parse_args()
    if args.command == 'show':
        from .record import show
        try:
            print(json.dumps(show(args.folder, args.node), indent=2, ensure_ascii=False))
        except ValueError as exc:
            parser.exit(2, f'{exc}\n')
    else:
        print(describe(args.folder, summary=args.summary, details=args.details))
