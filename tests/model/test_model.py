from copy import deepcopy
import hashlib
import json
import operator
import os
from pathlib import Path
import tempfile
import unittest

from mavc import Runner, verify
from mavc.model import check_settings, render, sources
from mavc.record import read_json, resolve
from mavc.runner import NewRunRequired
from mavc.operation import Operation, for_each
from pydantic import BaseModel, Field, ValidationError


def request():
    node = {'provider':'fixture','model':'fixture-model','reasoning':'high','timeout_seconds':30,
        'settings':{'tools':[],'transport':'sse','compaction':False,'cache_warming':'off',
                    'max_output_tokens':80,'temperature':.2},
        'pi_retry':{'agent_enabled':False,'provider_max_retries':0},'maximum_attempts':1,'pi_version':'1.0.4'}
    return {'node':node,'pi_version':'1.0.4','system_prompt':'fixture instructions','prompt':'Exact quote: café\n  preserved.'}


def response(req):
    node=req['node'];settings=node['settings']
    payload={'model':node['model'],'reasoning':{'effort':node['reasoning']},
             'max_output_tokens':settings.get('max_output_tokens'),'temperature':settings.get('temperature'),
             'tools':[], 'instructions':req['system_prompt']}
    return {'text':'{"result":"unchecked science"}','error':None,'stop_reason':'stop','exit_code':0,
        'actual_requests':[deepcopy(payload)],'upstream_response':{'model':node['model'],'status':'completed'},
        'observed':{'model':node['model'],'reasoning':node['reasoning'],'provider':'fixture',
          'active_tools':[],'pi_version':'1.0.4','request':{**payload,'transport':'sse'},'response':{'status':200},
          'resolved_settings':{'compaction':{'enabled':False},'cache_warming':'off','transport':'sse',
                              'retry':{'enabled':False,'maxRetries':0},'provider_retry':{'maxRetries':0}}}}


class Mention(Operation):
    """Report an explicit mention and keep its exact quotation."""
    class Input(BaseModel):
        text: str = Field(description='The complete paragraph, with whitespace unchanged.')
        data: dict = Field(default_factory=dict, description='Explicit auxiliary input data.')
    class Output(BaseModel):
        mentioned: bool = Field(description='Whether the paragraph explicitly mentions Parliament.')
        evidence: str = Field(description='Exact supporting text, or empty.')


def reply(req, *, text=None):
    result=response(req)
    if text is None:
        if req['prompt'].startswith('{'):
            text=json.loads(req['prompt']).get('input',{}).get('text','')
        else:text=req['prompt']
        text=json.dumps({'mentioned':'Parliament' in text,'evidence':text})
    result['text']=text
    return result


def ordinary(req):return reply(req)


class Legacy(Mention):
    def render(self, input, settings, children):
        return {'node':settings,'system_prompt':'legacy exact instructions','prompt':input.text,
                **{key:settings[key] for key in ('pi_version','pi_settings','route','terminal_tool') if key in settings}}


class CaptureCaller:
    def __init__(self, result=None):self.result=result;self.calls=0
    def __call__(self, req):
        self.calls+=1
        return deepcopy(self.result) if self.result is not None else reply(req)


class ModelTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.folder=Path(self.tmp.name)
        self.new={'provider':'fixture','model':'fixture-model','reasoning':'high'}
        self.old=request()['node']
    def runner(self, positions=None, folder=None):
        return Runner(folder or self.folder,{'flow_id':'signature-test',
            'positions':positions or {'Mention':self.new}})
    def evidence(self, run, name):
        refs=run.index()[name].details['evidence']
        self.assertEqual(len(refs),1)
        return resolve(run.folder,refs[0]).value
    def legacy(self, result, *, name='Legacy', folder=None):
        op=Legacy(caller=CaptureCaller(result))
        with self.runner({name:{'legacy':self.old}},folder) as run:
            op.name=name
            with self.assertRaises(Exception):op(run,{'text':'exact scientific input'})
            self.assertIsNone(run.index()[name].output);self.assertEqual(len(run.cache),0)
            retained=self.evidence(run,name)
            self.assertEqual(retained['response'],result)
        return retained

    def test_deterministic_signature_schemas_descriptions_and_new_defaults(self):
        op=Mention(caller=ordinary)
        with self.runner() as run:
            node=run.declaration.for_position('Mention')['node']
            value=Mention.Input(text='café\n  Parlement',data={'b':2,'a':1})
            first=render(op,value,node,None)
            second=render(op,Mention.Input(text=value.text,data={'a':1,'b':2}),node,None)
            self.assertEqual(first,second);self.assertEqual(first['node'],node)
            body=json.loads(first['prompt'])
            self.assertEqual(body['input']['text'],value.text)
            self.assertIn('whitespace unchanged',body['input_schema']['properties']['text']['description'])
            self.assertIn('Exact supporting',body['output_schema']['properties']['evidence']['description'])
            self.assertEqual(first['system_prompt'],Mention.__doc__)
            self.assertEqual(node['timeout_seconds'],600)
            self.assertEqual(node['settings'],{'tools':[],'compaction':False,'cache_warming':'off','transport':'sse'})
            self.assertEqual(node['pi_retry'],{'agent_enabled':False,'provider_max_retries':0})
            actual=op(run,{'text':'Parliament\n  exact'})
            self.assertTrue(actual.mentioned)
            raw=self.evidence(run,'Mention')
            self.assertEqual(raw['renderer'],'signature');self.assertEqual(raw['settings_check']['mismatches'],[])
            self.assertEqual(json.loads(raw['request']['prompt'])['input']['text'],'Parliament\n  exact')
            self.assertEqual(run.declaration.snapshot()['positions']['Mention'],node)
            self.assertFalse(verify(run.folder)['completed'])  # Typed success is not scientific flow completion.

    def test_legacy_render_exact_and_one_cached_typed_output_with_one_raw_artifact(self):
        caller=CaptureCaller();op=Legacy(caller=caller)
        declaration={'Legacy':{'legacy':self.old}}
        with self.runner(declaration) as run:
            first=op(run,{'text':'Parliament café\n  source'})
            node=run.index()['Legacy'];raw=self.evidence(run,'Legacy')
            self.assertEqual(raw['request'],op.render(Legacy.Input(text='Parliament café\n  source'),self.old,None))
            self.assertEqual(raw['renderer'],'custom')
            self.assertEqual(raw['response']['text'],json.dumps(first.model_dump()))
            self.assertNotEqual(node.output,node.details['evidence'][0])
            self.assertEqual(run.declaration.snapshot()['positions']['Legacy'],{'legacy':self.old})
        with self.runner(declaration) as run:
            self.assertEqual(op(run,{'text':'Parliament café\n  source'}),first)
            self.assertTrue(run.index()['Legacy'].reused);self.evidence(run,'Legacy')
        self.assertEqual(caller.calls,1)
        artifacts=[read_json(p) for p in (self.folder/'outputs/objects').glob('*.json')]
        self.assertEqual(sum(a['type']=='model-call/v1' for a in artifacts),1)
        self.assertFalse((self.folder/'calls').exists())  # Evidence belongs to canonical node details.

    def test_all_observable_mutations_and_partial_errors_retain_raw_and_never_cache(self):
        req=request();good=response(req)
        mutations=[('upstream_response.model',lambda r:r['upstream_response'].update(model='wrong')),
          ('node.model',lambda r:r['observed'].update(model='wrong')),
          ('reasoning',lambda r:r['actual_requests'][0]['reasoning'].update(effort='low')),
          ('max_output_tokens',lambda r:r['actual_requests'][0].update(max_output_tokens=99)),
          ('temperature',lambda r:r['observed']['request'].update(temperature=.8)),
          ('compaction',lambda r:r['observed']['resolved_settings']['compaction'].update(enabled=True)),
          ('cache_warming',lambda r:r['observed']['resolved_settings'].update(cache_warming='streaming')),
          ('transport',lambda r:r['observed']['request'].update(transport='websocket')),
          ('agent_enabled',lambda r:r['observed']['resolved_settings']['retry'].update(enabled=True)),
          ('provider_max_retries',lambda r:r['observed']['resolved_settings']['provider_retry'].update(maxRetries=2)),
          ('pi_version',lambda r:r['observed'].update(pi_version='changed')),
          ('HTTP429',lambda r:r.update(error='HTTP429 capacity',provider_attempts=[{'status':429,'attempt':1}])),
          ('partial',lambda r:r.update(stop_reason='length')),
          ('incomplete',lambda r:r['upstream_response'].update(status='incomplete')),
          ('empty',lambda r:r.update(text='')),
          ('toolUse',lambda r:r.update(stop_reason='toolUse'))]
        for i,(label,mutate) in enumerate(mutations):
            with self.subTest(label=label):
                bad=deepcopy(good);mutate(bad)
                checks=check_settings(req,bad);self.assertTrue(checks['mismatches'])
                retained=self.legacy(bad,folder=self.folder/str(i))
                self.assertEqual(retained['settings_check']['mismatches'],checks['mismatches'])

    def test_later_captured_identity_missing_is_failed_and_historical_capture_is_explicit(self):
        good=response(request())
        for i,field in enumerate(('model','reasoning')):
            bad=deepcopy(good);bad['actual_requests'].append(deepcopy(bad['actual_requests'][0]))
            del bad['actual_requests'][1][field]
            self.legacy(bad,folder=self.folder/str(i))
        historical=deepcopy(good);del historical['actual_requests'];del historical['observed']['resolved_settings']
        checks=check_settings(request(),historical)
        self.assertEqual(checks['mismatches'],[])
        self.assertIn('actual_requests',checks['unobserved']);self.assertIn('node.settings.compaction',checks['unobserved'])

    def test_actual_tool_schema_strictness_and_terminal_proposal_are_retained(self):
        node={**self.old,'tools':['submit'],'terminal_tool':'submit',
              'pool_transport':{'version':'bb-responses-nonstrict-v1','ordinary_function_strict':False}}
        req={'node':node,'pi_version':node['pi_version'],'terminal_tool':'submit',
             'system_prompt':'legacy exact instructions','prompt':'Exact scientific input'}
        good=response(req)
        tool={'type':'function','name':'submit','strict':False,
              'parameters':{'type':'object','properties':{'evidence':{'type':'string'}}}}
        good.update(stop_reason='toolUse',text='acknowledgment',proposal={'mentioned':False,'evidence':req['prompt']})
        good['observed']['active_tools']=['submit']
        good['observed']['request'].update(tools=[deepcopy(tool)],pool_transport=node['pool_transport'])
        good['actual_requests'][0]['tools']=[deepcopy(tool)]
        declaration={'Legacy':{'legacy':node}}
        with self.runner(declaration,self.folder/'good') as run:
            value=Legacy(caller=CaptureCaller(good))(run,{'text':req['prompt']})
            self.assertEqual(value.evidence,req['prompt'])
            self.assertEqual(self.evidence(run,'Legacy')['response'],good)
        for label,mutate in [('strict',lambda r:r['actual_requests'][0]['tools'][0].update(strict=True)),
                             ('missing proposal',lambda r:r.update(proposal=None))]:
            with self.subTest(label=label),self.runner(declaration,self.folder/label) as run:
                bad=deepcopy(good);mutate(bad)
                with self.assertRaises(ValueError):Legacy(caller=CaptureCaller(bad))(run,{'text':req['prompt']})
                self.assertEqual(self.evidence(run,'Legacy')['response'],bad)
                self.assertIsNone(run.index()['Legacy'].output);self.assertEqual(len(run.cache),0)

    def test_undeclared_root_override_is_refused_before_caller(self):
        class Bad(Mention):
            def render(self,input,settings,children):
                value=render(self,input,settings,children)
                value['pi_settings']={'retry':{'enabled':True}}
                return value
        caller=CaptureCaller();op=Bad(caller=caller)
        with self.runner({'Bad':self.new}) as run:
            with self.assertRaisesRegex(RuntimeError,'Rendered request settings differ'):op(run,{'text':'source'})
            raw=self.evidence(run,'Bad')
            self.assertIsNone(raw['response']);self.assertEqual(len(run.cache),0)
            self.assertIn('pi_settings',raw['request'])
        self.assertEqual(caller.calls,0)

    def test_missing_position_and_conflicting_retry_ownership_do_not_call(self):
        caller=CaptureCaller();op=Mention(caller=caller)
        with self.runner({'Other':self.new}) as run:
            with self.assertRaisesRegex(ValueError,'requires provider'):op(run,{'text':'source'})
            self.assertEqual(run.index(), {})
        invalid={**self.new,'maximum_attempts':2,'pi_retry':{'agent_enabled':True}}
        with self.assertRaisesRegex(ValueError,'one outer'):
            self.runner({'Mention':invalid},self.folder/'retry')
        self.assertEqual(caller.calls,0)

    def test_parse_and_typed_output_failures_keep_attached_raw_evidence(self):
        class BadOutput(Mention):
            def parse(self,request,response,input,children):return {'mentioned':'not-a-boolean','evidence':''}
        with self.runner({'BadOutput':self.new}) as run:
            with self.assertRaises(ValidationError):BadOutput(caller=ordinary)(run,{'text':'Parliament'})
            raw=self.evidence(run,'BadOutput')
            self.assertEqual(raw['settings_check']['mismatches'],[])
            self.assertIsNone(run.index()['BadOutput'].output);self.assertEqual(len(run.cache),0)
            self.assertTrue(verify(run.folder)['verified'])
        def duplicated(req):return reply(req,text='{"mentioned":true,"mentioned":false,"evidence":""}')
        with self.runner(folder=self.folder/'json') as run:
            with self.assertRaisesRegex(ValueError,'Duplicate JSON key'):Mention(caller=duplicated)(run,{'text':'source'})
            self.assertIn('mentioned',self.evidence(run,'Mention')['response']['text'])
            self.assertEqual(len(run.cache),0)

    def test_concurrent_distinct_evidence_on_one_shared_stateless_operation(self):
        op=Mention(caller=ordinary)
        root=for_each('Batch',op,key=lambda item:item['data']['id'])
        positions={'Batch':{'max_concurrency':2},'Batch.Mention':self.new}
        with self.runner(positions) as run:
            results=root(run,[{'text':'Parliament A','data':{'id':'a'}},{'text':'No mention B','data':{'id':'b'}}])
            self.assertEqual(len(results),2)
            a,b=self.evidence(run,'Batch/a'),self.evidence(run,'Batch/b')
            self.assertNotEqual(run.index()['Batch/a'].details['evidence'],run.index()['Batch/b'].details['evidence'])
            self.assertEqual(json.loads(a['request']['prompt'])['input']['text'],'Parliament A')
            self.assertEqual(json.loads(b['request']['prompt'])['input']['text'],'No mention B')
            self.assertFalse(any(key in op.__dict__ for key in ('settings','evidence','attempt','request')))
            run.finish(results);self.assertTrue(verify(run.folder)['completed'])

    def test_default_and_custom_parser_and_caller_sources_before_cache(self):
        declared = sources(Mention(caller=ordinary))
        self.assertIn(json.loads, declared)
        with self.runner(folder=self.folder/'missing-caller') as run:
            with self.assertRaisesRegex(ValueError, 'require caller='):
                Mention()(run, {'text': 'source'})
        custom=Legacy(caller=ordinary)
        self.assertIn(custom.render,sources(custom))
        class Parsed(Mention):
            def parse(self,request,response,input,children):return {'mentioned':False,'evidence':''}
        parsed=Parsed(caller=ordinary)
        self.assertIn(parsed.parse,sources(parsed));self.assertNotIn(json.loads,sources(parsed))
        with self.runner({'Parsed':self.new}) as run:
            parsed(run,{'text':'source'})
            implementation=run.index()['Parsed'].implementation
            self.assertTrue(any('Parsed.parse' in key for key in implementation))
            identity=run.index()['Parsed'].declaration['operation']['caller']
            self.assertTrue(any('ordinary' in key for key in identity))
            self.assertIn(hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),identity.values())

    def test_opaque_caller_identity_explicit_source_escape_changes_before_cache(self):
        class Opaque(Mention):
            def render(self,input,settings,children):
                value=render(self,input,settings,children);value['reply']=reply(value)
                return value
        with self.runner({'Opaque':self.new}) as run:
            with self.assertRaisesRegex(ValueError,'No defining source'):
                Opaque(caller=operator.itemgetter('reply'))(run,{'text':'source'})
            op=Opaque(caller=operator.itemgetter('reply'),sources=(Path(__file__),))
            op(run,{'text':'source'})
            self.assertIn('explicit-boundary',str(run.index()['Opaque'].declaration['operation']['caller']))
        with self.runner({'Opaque':self.new}) as run:
            other=Opaque(caller=operator.attrgetter('reply'),sources=(Path(__file__),))
            with self.assertRaises(NewRunRequired):other(run,{'text':'source'})
            self.assertTrue(run.index()['Opaque'].output)

