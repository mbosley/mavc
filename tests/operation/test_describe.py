import hashlib
import importlib
import json
from pathlib import Path
import subprocess
import sys
import tomllib
from types import ModuleType

from mavc.describe import describe
from mavc.record import write_json


def node(name, status='completed', **fields):
    return {'node': name, 'declaration': {}, 'outcome': {'status': status}, **fields}


def fixture(folder):
    failed = node('opaque-old-local', 'failed', purpose='Try the local answer.',
        position='Method.Choose.Local', details={'evidence': [{'type': 'model-call/v1', 'sha256': 'call'}]})
    answer = node('opaque-answer', purpose='Return the frontier answer.',
        position='Method.Choose.Frontier', attempts=1, reused=True)
    parent = node('opaque-choice', composition='route', position='Method.Choose',
        purpose='Use the frontier if the local answer is unavailable.',
        children={'opaque-old-local': failed, 'opaque-answer': answer},
        declaration={'operation': {'available_children': ['Local', 'Frontier', 'Unused']}},
        details={'selected': 'opaque-answer', 'route': {'route': 'frontier', 'model': 'Frontier',
            'fallback_reason': 'local_failure', 'local_error': 'retained local error', 'cutoff': .8}})
    empty = node('opaque-tables', composition='for-each', position='Method.Tables',
                 purpose='Read the selected tables.', children={})
    loop = node('opaque-loop', composition='iterate', purpose='Refine the draft.',
                details={'iterations': 2, 'stopped': 'bound'}, children={})
    write_json(folder/'record.json', {'outcome': {'status': 'completed'},
        'nodes': {'opaque-choice': parent, 'opaque-tables': empty, 'opaque-loop': loop},
        'outputs': {'type': 'report/v1', 'sha256': 'report'}})
    write_json(folder/'outputs/objects/call.json', {'value': {'response': {'error': 'failed'}}})
    write_json(folder/'outputs/objects/report.json', {'value': {'rows': [1, 2, 3],
        'positive_ids': ['a', 'c'], 'negative_ids': ['b'], 'accuracy': None,
        'every_quote_exact': True, 'scientific_acceptance': 'accepted'}})


def snapshot(folder):
    return {str(p.relative_to(folder)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in folder.rglob('*') if p.is_file()}


def test_groups_twelve_batches_and_three_sentences_with_normalized_purposes(tmp_path):
    batches = {f'opaque-batch-{i}': node(f'opaque-batch-{i}', position='Method.Batches.Code',
        purpose='Classify each batch.\n    Keep its recorded disposition.', attempts=1) for i in range(12)}
    sentences = {f'opaque-sentence-{i}': node(f'opaque-sentence-{i}', position='Method.Sentences.Tone',
        purpose='  Describe the tone.\n      Preserve its label.') for i in range(3)}
    sentences['opaque-sentence-1']['purpose'] = 'Describe the tone.\n  Preserve its label.'
    write_json(tmp_path/'record.json', {'outcome': {'status': 'completed'}, 'nodes': {**batches, **sentences}})
    before = snapshot(tmp_path)
    text = describe(tmp_path, summary=True)
    assert text.count('Classify each batch. Keep its recorded disposition') == 1
    assert text.count('Describe the tone. Preserve its label') == 1
    assert '(12 times)' in text and '(3 times)' in text
    assert text.count('status: completed') == 1
    assert not any(word in text for word in ('opaque-', 'Method.', 'attempts', 'templates', 'sha256'))
    assert snapshot(tmp_path) == before


def test_different_outcomes_reasons_positions_and_forms_are_not_folded(tmp_path):
    common = {'purpose': 'Apply the coding question.', 'position': 'Method.Code'}
    nodes = {str(i): node(str(i), **common) for i in range(2)}
    nodes['bad-a'] = node('bad-a', 'failed', **common)
    nodes['bad-b'] = node('bad-b', 'failed', **common)
    nodes['bad-a']['outcome']['reason'] = 'empty result'
    nodes['bad-b']['outcome']['reason'] = 'capacity failure'
    nodes['other'] = node('other', purpose=common['purpose'], position='Method.Other')
    nodes['parent'] = node('parent', composition='custom', **common, children={})
    write_json(tmp_path/'record.json', {'outcome': {'status': 'failed'}, 'nodes': nodes})
    text = describe(tmp_path)
    assert text.count('Apply the coding question') == 5
    assert '(2 times)' in text
    assert text.count('status: failed') == 3
    assert 'steps: []' in text


def test_route_choices_failed_selection_and_unknown_branches_remain_distinct(tmp_path):
    nodes = {}
    for name, route, model, status in [('local', 'local', 'Local', 'completed'),
                                      ('fallback', 'frontier', 'Frontier', 'completed'),
                                      ('failure', 'frontier', 'Frontier', 'failed')]:
        nodes[name] = node(name, status, purpose='Select an answer.', position='Method.Choose', composition='route',
            details={'selected': f'{name}/opaque-selected', 'route': {'route': route, 'model': model,
                'fallback_reason': 'local_failure' if route == 'frontier' else None,
                'error': 'frontier failed' if status == 'failed' else None}})
    for name in ('unknown-a', 'unknown-b'):
        nodes[name] = node(name, purpose='Select an answer.', position='Method.Choose', composition='route',
                           details={'selected': name+'/opaque-selected'})
    write_json(tmp_path/'record.json', {'outcome': {'status': 'failed'}, 'nodes': nodes})
    text = describe(tmp_path)
    assert text.count('Select an answer') == 5
    assert '"route": "local"' in text and '"model": "Local"' in text
    assert text.count('"route": "frontier"') == 2
    assert text.count('steps: []') == 5
    assert 'opaque-selected' not in text
    detailed = describe(tmp_path, details=True)
    assert 'frontier failed' in detailed and 'unknown-b/opaque-selected' in detailed


def test_details_preserves_full_canonical_record_without_duplicate_attempt_totals(tmp_path):
    fixture(tmp_path)
    before = snapshot(tmp_path)
    expected = json.loads((tmp_path/'record.json').read_text())
    detailed = describe(tmp_path, summary=True, details=True)
    raw = detailed.split('not execution counts):\n', 1)[1]
    assert json.loads(raw) == expected
    assert expected['nodes']['opaque-choice']['children']['opaque-answer']['attempts'] == 1
    assert expected['nodes']['opaque-choice']['children']['opaque-answer']['reused'] is True
    assert 'Model-call evidence references:' not in detailed
    assert all(field in detailed for field in ('opaque-answer', 'Method.Choose', 'available_children',
                                               'local_error', 'cutoff', 'report/v1', 'sha256'))
    assert snapshot(tmp_path) == before


def test_results_are_only_recorded_quantities_and_outcomes_not_scientific_claims(tmp_path):
    fixture(tmp_path)
    text = describe(tmp_path, summary=True)
    results = text.split('results:\n', 1)[1]
    assert results == '  rows: 3\n  positive_ids: 2\n  negative_ids: 1\n  failed steps: 1'
    assert all(word not in results for word in ('quote', 'accuracy', 'acceptance', 'accepted'))
    assert 'step: Try the local answer.\n        status: failed' in text
    assert '"route": "frontier"' in text and '"fallback_reason": "local_failure"' in text
    assert 'iterations: 2' in text and 'stopped: "bound"' in text and 'steps: []' in text
    assert 'Unused' not in text and 'attempts' not in text
    write_json(tmp_path/'outputs/objects/report.json', {'value': {'score': 0.25, 'is_exact': False}})
    assert '  score: 0.25' in describe(tmp_path, summary=True)
    assert 'is_exact' not in describe(tmp_path, summary=True)


def test_empty_and_nonempty_loops_and_different_stops_are_not_folded(tmp_path):
    same = {'purpose': 'Process the supplied sentences.', 'position': 'Method.Sentences', 'composition': 'for-each'}
    empty = node('empty', **same, children={})
    full = node('full', **same, children={'sentence': node('sentence', purpose='Read a sentence.')})
    loops = {reason: node(reason, composition='iterate', position='Method.Refine', purpose='Refine the draft.',
                         details={'iterations': 2, 'stopped': reason}) for reason in ('bound', 'condition')}
    write_json(tmp_path/'record.json', {'outcome': {'status': 'completed'}, 'nodes': {'empty': empty, 'full': full, **loops},
                                      'outputs': {'type': 'list/v1', 'sha256': 'empty'}})
    write_json(tmp_path/'outputs/objects/empty.json', {'value': []})
    text = describe(tmp_path, summary=True)
    assert text.count('Process the supplied sentences') == 2
    assert text.count('Refine the draft') == 2
    assert 'iterations: 2' in text and 'stopped: "bound"' in text and 'stopped: "condition"' in text
    assert 'results:\n  output items: 0\n  failed steps: 0' in text


def test_stdlib_renderer_and_legacy_flat_records(tmp_path, monkeypatch):
    alias = ModuleType('outline_mavc'); alias.__path__ = [str(Path(__file__).parents[2]/'src/mavc')]
    monkeypatch.setitem(sys.modules, 'outline_mavc', alias)
    renderer = importlib.import_module('outline_mavc.describe')
    write_json(tmp_path/'record.json', {'outcome': {'status': 'failed'}, 'nodes': {
        key: {'declaration': {}, 'outcome': {'status': 'failed'}, 'attempts': 1}
        for key in ('opaque-legacy-a', 'opaque-legacy-b')}})
    before = snapshot(tmp_path)
    text = renderer.describe(tmp_path, summary=True)
    assert text.count('step: Recorded operation') == 2
    assert text.count('status: failed') == 3
    assert 'opaque-legacy' not in text
    detailed = renderer.describe(tmp_path, details=True)
    assert 'opaque-legacy-a' in detailed and '"attempts": 1' in detailed
    assert snapshot(tmp_path) == before
    assert not any(name.startswith('outline_mavc.') and name not in {'outline_mavc.describe', 'outline_mavc.tree'} for name in sys.modules)


def test_public_import_and_both_cli_paths_use_one_details_parser(tmp_path):
    fixture(tmp_path)
    code = ('import sys; from mavc import Operation, Settings, sequence, walk_nodes; '
            'import mavc.route; assert callable(mavc.route.route); '
            'assert "mavc.model" not in sys.modules; assert "pi_call" not in sys.modules; '
            'assert "torch" not in sys.modules')
    subprocess.run([sys.executable, '-c', code], cwd=tmp_path, check=True, timeout=5)
    metadata = tomllib.loads((Path(__file__).parents[2]/'pyproject.toml').read_text())
    assert metadata['project']['scripts']['mavc'] == 'mavc.describe:main'
    for flags in (['--summary'], ['--summary', '--details']):
        command = ['describe', str(tmp_path), *flags]
        module = subprocess.run([sys.executable, '-m', 'mavc', *command], cwd=tmp_path,
                                text=True, capture_output=True, check=True, timeout=5)
        console = subprocess.run([sys.executable, '-c', 'from mavc.describe import main; main()', *command],
                                 cwd=tmp_path, text=True, capture_output=True, check=True, timeout=5)
        assert module.stdout == console.stdout and not module.stderr and not console.stderr
        assert 'results:\n  rows: 3' in module.stdout
        assert ('opaque-answer' in module.stdout) == ('--details' in flags)


def test_nested_asks_schema_descriptions_and_configured_identity(tmp_path):
    ask = node('ask', purpose='Does this paragraph propose an action? Keep all later instructions.',
        position='Flow.Each.Pair.Ask', declaration={'node': {'model': 'recorded-model', 'reasoning': 'medium'},
        'operation': {'caller': {}, 'signature': {'output': {'properties': {
            'answer': {'type': 'boolean', 'description': 'Whether an action is proposed.'},
            'quote': {'type': 'string', 'description': 'Literal supporting quotation.'}}}}}})
    pair = node('pair', purpose='Ask and check one paragraph.', composition='sequence', position='Flow.Each.Pair',
                children={'ask': ask, 'check': node('check', purpose='Check literal quotation.')})
    each = node('each', purpose='Read each paragraph.', composition='for-each', position='Flow.Each',
                children={str(i): pair for i in range(4)})
    root = node('flow', purpose='Read the municipal plan.', composition='sequence',
                children={'each': each, 'inventory': node('inventory', purpose='Check full inventory.')})
    write_json(tmp_path/'record.json', {'outcome': {'status': 'completed'}, 'nodes': {'flow': root}})
    text = describe(tmp_path)
    assert text.startswith('flow: Read the municipal plan.\nstatus: completed\nsteps:\n')
    assert '  - for each: Read each paragraph. (4): Ask and check one paragraph.\n    steps:' in text
    assert 'sequence:' not in text
    assert '      - ask: Does this paragraph propose an action?\n        model (configured): recorded-model (medium)' in text
    assert '        returns:\n          - Whether an action is proposed.\n          - Literal supporting quotation.' in text
    assert text.count('status:') == 1 and 'later instructions' not in text
    assert 'later instructions' in describe(tmp_path, details=True)
    assert text.endswith('  - check: Check full inventory.')
    pair['outcome']['status'] = 'failed'
    write_json(tmp_path/'record.json', {'outcome': {'status': 'failed'}, 'nodes': {'flow': root}})
    failed = describe(tmp_path)
    assert 'sequence: Ask and check one paragraph. (4 times)\n        status: failed' in failed
    pair['outcome']['status'] = 'completed'
    pair['details'] = {'stopped': 'review needed'}
    write_json(tmp_path/'record.json', {'outcome': {'status': 'completed'}, 'nodes': {'flow': root}})
    assert 'sequence: Ask and check one paragraph. (4 times)' in describe(tmp_path)


def test_parent_paths_full_instructions_schema_and_model_prevent_false_grouping(tmp_path):
    def ask(model, instruction, description):
        return node('ask', position='Flow.Ask', purpose='Ask the question. ' + instruction,
            declaration={'node': {'model': model}, 'operation': {'signature': {
                'output': {'properties': {'value': {'description': description}}}}}})
    nodes = {'one': ask('A', 'First rule.', 'First value.'),
             'two': ask('A', 'Second rule.', 'First value.'),
             'three': ask('B', 'First rule.', 'First value.'),
             'four': ask('A', 'First rule.', 'Second value.')}
    nodes['branch'] = node('branch', composition='sequence', purpose='Run the alternative.',
                           children={'one': nodes['one']})
    write_json(tmp_path/'record.json', {'outcome': {'status': 'completed'}, 'nodes': nodes})
    text = describe(tmp_path)
    assert text.count('ask: Ask the question.') == 5
    assert 'times)' not in text
    assert '  - sequence: Run the alternative.\n    steps:\n      - ask: Ask the question.' in text


def test_repeated_routes_hide_answer_payloads_and_ids_but_retain_choice(tmp_path):
    nodes = {key: node(key, composition='route', position='Flow.Choose', purpose='Select an answer.',
        details={'selected': key + '/frontier', 'route': {'route': 'frontier', 'model': 'Frontier',
            'fallback_reason': 'low_confidence', 'sample_id': key, 'answer': {'private_row': key}}})
        for key in ('opaque-one', 'opaque-two')}
    write_json(tmp_path/'record.json', {'outcome': {'status': 'completed'}, 'nodes': nodes})
    text = describe(tmp_path)
    assert text.count('route: Select an answer.') == 1 and '(2 times)' in text
    assert '"fallback_reason": "low_confidence"' in text
    assert 'opaque-' not in text and 'private_row' not in text
    assert 'private_row' in describe(tmp_path, details=True)


def test_summary_explains_the_failure_at_its_node(tmp_path):
    write_json(tmp_path/'record.json', {'outcome': {'status': 'failed', 'reason': 'No checked submission accepted'},
        'nodes': {'Agent': node('Agent', 'failed', outcome={'status': 'failed', 'reason': 'No checked submission accepted'})},
        'outputs': None})
    text = describe(tmp_path, summary=True)
    assert 'failure (Agent): No checked submission accepted' in text
    assert 'failed steps: 1' in text
