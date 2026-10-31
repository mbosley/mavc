import json
from pathlib import Path
import tempfile
import unittest
from mavc.learn import Admission, Candidate, Decision, Example, admit, collect, export, check_splits, split


def example(doc='doc',pdf='pdf',**meta):
    return Example('passage',{'units':[]},{'document_id':doc,'pdf_sha256':pdf,'country':'country-a','split':'train',**meta})


class LearnTests(unittest.TestCase):
    def test_failed_rejected_and_failed_tool_targets_never_project(self):
        called=[]
        candidates=[Candidate('a','failed',output='tempting'),Candidate('b','rejected',output='tempting'),
                    Candidate('c','completed',failed_tool_call=True),Candidate('d','completed',output={})]
        def project(candidate):
            called.append(candidate.id);return example()
        decisions=list(admit(candidates,project))
        self.assertEqual(called,['d'])
        self.assertEqual([a.decision.status for a in decisions],['excluded']*3+['included'])
        self.assertTrue(all(a.example is None for a in decisions[:3]))

    def test_empty_negative_does_not_need_transcript(self):
        result=list(admit([Candidate('empty','completed')],lambda c:example()))[0]
        self.assertEqual(result.example.target,{'units':[]})
        self.assertEqual(result.decision.status,'included')

    def test_projection_missing_fields_quarantined_with_reason(self):
        result=list(admit([Candidate('a','completed',output={})],lambda c: c.output['missing']))[0]
        self.assertEqual(result.decision.status,'quarantined')
        self.assertIn('missing',result.decision.reason)

    def test_human_review_only_in_heldout(self):
        candidate=Candidate('human','completed')
        result=list(admit([candidate],lambda c:example(label_source='human_review')))[0]
        self.assertEqual(result.decision.status,'quarantined')
        result=list(admit([candidate],lambda c:example(label_source='human_review',split='test')))[0]
        self.assertEqual(result.decision.status,'included')
        with self.assertRaises(ValueError):check_splits([example(label_source='human_review').metadata])

    def test_projection_cannot_relabel_human_answers_as_teacher(self):
        result=list(admit([Candidate('human','completed',metadata={'label_source':'human_review'})],
                          lambda c:example(label_source='teacher')))[0]
        self.assertEqual(result.decision.status,'quarantined')

    def test_export_cannot_label_manifest_train_and_rows_test(self):
        projected=example(split='test')
        admission=Admission('x',Decision(),example(split='train').metadata,projected)
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):export([admission],Path(tmp)/'data')
            self.assertFalse((Path(tmp)/'data/manifest.json').exists())

    def test_document_alias_and_family_leakage(self):
        for metas,fields in [([example().metadata,example('alias',split='test').metadata],('document_id','pdf_sha256')),
                             ([example(family='family').metadata,example('other','other',family='family',split='test').metadata],('family',))]:
            with self.assertRaises(ValueError):check_splits(metas,group_fields=fields)
        with self.assertRaises(ValueError):check_splits([example(country='country-b').metadata],heldout_countries={'country-b':'country_test'})

    def test_seeded_whole_groups_join_transitive_aliases(self):
        data=[example('a','one'),example('b','one'),example('b','two'),example('c','three',country='country-b')]
        result=split(data,seed=4,heldout_countries={'country-b':'country_test'})
        self.assertEqual(len({r.metadata['split'] for r in result[:3]}),1)
        self.assertEqual(result[3].metadata['split'],'country_test')
        reverse=split(reversed(data),seed=4,heldout_countries={'country-b':'country_test'})
        self.assertEqual({(r.metadata['document_id'],r.metadata['pdf_sha256']):r.metadata['split'] for r in result},
                         {(r.metadata['document_id'],r.metadata['pdf_sha256']):r.metadata['split'] for r in reverse})

    def test_human_alias_stays_with_reserved_country(self):
        data=[example('a','one',label_source='human_review',country='country-b'),
              example('b','one',country='country-b')]
        result=split(data,heldout_countries={'country-b':'country_test'})
        self.assertEqual([r.metadata['split'] for r in result],['country_test','country_test'])

    def test_nonfinite_split_weights_are_not_a_split(self):
        with self.assertRaises(ValueError):split([example()],fractions={'train':float('nan')})
        with self.assertRaises(ValueError):check_splits([example().metadata],group_fields=())

    def test_export_accounts_for_every_candidate_and_keeps_duplicates(self):
        candidates=[Candidate(str(i),'completed',metadata=example().metadata) for i in range(3)]
        candidates.append(Candidate('failed','failed'))
        with tempfile.TemporaryDirectory() as tmp:
            result=export(admit(candidates,lambda c:example(),policy=lambda e:Decision()),Path(tmp)/'data')
            self.assertEqual(result['candidates'],4)
            self.assertEqual(result['decisions'],{'included':3,'excluded':1,'quarantined':0})
            self.assertEqual(len((Path(tmp)/'data/train.jsonl').read_text().splitlines()),3)
            self.assertEqual(len((Path(tmp)/'data/admissions.jsonl').read_text().splitlines()),4)

    def test_leaky_export_never_publishes_manifest(self):
        candidates=[Candidate('a','completed'),Candidate('b','completed')]
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp)/'data'
            with self.assertRaises(ValueError):
                export(admit(candidates,lambda c:example(split='train' if c.id=='a' else 'test')),folder)
            self.assertFalse((folder/'manifest.json').exists())

    def test_collect_retains_outcomes_and_accepted_projection_boundary(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp);nodes={name:{'kind':'reader','input':{'id':'input'},'output':{'id':'output'},
                'outcome':{'status':status,'reason':'retained explanation'}} for name,status in
                [('call:x','completed'),('check:x','completed'),('check:y','rejected')]}
            (path/'record.json').write_text(json.dumps({'nodes':nodes,'metadata':{}}))
            candidates=list(collect([path],resolver=lambda folder,ref:ref['id']))
            def project(c):
                return example() if c.context['node']['outcome']['status']=='completed' and c.metadata['node'].startswith('check:') else Decision('excluded','Unchecked model call')
            decisions=list(admit(candidates,project))
            self.assertEqual([a.decision.status for a in decisions],['excluded','included','excluded'])
            self.assertEqual(len(decisions),3)


if __name__=='__main__':unittest.main()
