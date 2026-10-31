import json
import unittest
from pathlib import Path
import numpy as np
from mavc.evals import (Cell, Matrix, Validation, agreement, evaluate_test, fit_policy,
                        multiset_overlap, multiset_stability, overlap, probability_metrics, review_sample, scaled_probabilities,
                        selective_metrics, stability)


class MatrixTests(unittest.TestCase):
    def test_missing_failed_and_abstain_are_distinct(self):
        matrix = Matrix(['one','two','three'], ['a','b'], [
            Cell('one','a',answer=None), Cell('two','a','failed',reason='transport'),
            Cell('three','a',answer='yes')])
        self.assertEqual(matrix.counts()['a'], {'assessed':2,'failed':1,'missing':0})
        self.assertEqual(matrix.counts()['b'], {'assessed':0,'failed':0,'missing':3})
        scored = matrix.score({'one':None},lambda answer,target: answer==target)
        self.assertTrue(scored[0]['score'])
        self.assertTrue(scored[-2]['reference_missing'])
        self.assertIsNone(scored[-2]['score'])

    def test_invalid_cells_cannot_overwrite(self):
        with self.assertRaises(ValueError):
            Matrix(['a'],['b'],[Cell('a','b'),Cell('a','b')])
        with self.assertRaises(ValueError):
            Matrix(['a'],['b'],[Cell('outside','b')])
        with self.assertRaises(ValueError):
            Cell('a','b','failed')
        with self.assertRaises(ValueError):
            Cell('a','b','missing',answer='yes')

    def test_review_covers_every_failure_and_missing(self):
        cells = [Cell(str(i),'a',answer=i) for i in range(12)]
        cells += [Cell('failed','a','failed',reason='error')]
        matrix = Matrix([str(i) for i in range(12)]+['failed','missing'],['a'],cells)
        failure = lambda cell: cell.answer == 3
        sample = review_sample(matrix,size=4,seed=42,failure=failure)
        self.assertEqual(len(sample),7)
        self.assertTrue({'failed','missing','3'} <= {cell.case for cell in sample})
        reordered = Matrix(matrix.cases,matrix.candidates,reversed(matrix.cells))
        self.assertEqual(sample,review_sample(reordered,size=4,seed=42,failure=failure))


class MetricsTests(unittest.TestCase):
    def test_abstentions_count_as_answers(self):
        result = agreement(['a','b','b'],['a',None,'b'],labels=['a','b',None])
        self.assertEqual(result['n'],3)
        self.assertEqual(result['matches'],2)
        self.assertEqual(result['confusion'],[[1,0,0],[0,1,1],[0,0,0]])
        self.assertAlmostEqual(result['kappa'],.5)
        self.assertIsNone(agreement(['a'],['a'],labels=['a','b'])['kappa'])
        with self.assertRaises(ValueError):
            agreement(['a'],['unknown'],labels=['a','b'])

    def test_missing_consensus_is_not_a_disagreement_or_denominator(self):
        p = np.array([[.95,.05],[.05,.95],[.95,.05]])
        result = selective_metrics(p,{'consensus':{0:'a',2:'b'}},labels=['a','b'],cutoff=.9)
        self.assertEqual(result['n'],3)
        self.assertEqual(result['references']['consensus']['n'],2)
        self.assertEqual(result['references']['consensus']['matches'],1)
        self.assertEqual(result['references']['consensus']['selected_reference_missing'],1)
        self.assertIsNotNone(result['coverage_wilson_95'])

    def test_no_coverage_is_undefined_agreement(self):
        result = selective_metrics([[.5,.5]],{'coder':{0:'a'}},labels=['a','b'],cutoff=.9)
        self.assertEqual(result['coverage'],0)
        self.assertIsNone(result['references']['coder']['agreement'])
        empty = probability_metrics(np.empty((0,2)),[],labels=['a','b'])
        self.assertEqual(empty['confusion'],[[0,0],[0,0]])
        self.assertIsNone(empty['nll'])
        json.dumps(empty,allow_nan=False)

    def test_probability_validation_and_bins(self):
        result = probability_metrics([[1,0],[.5,.5]],['a','b'],labels=['a','b'])
        self.assertEqual(sum(b['n'] for b in result['reliability']),2)
        self.assertEqual(result['matches'],1)  # caller label order breaks ties
        self.assertEqual(result['reliability'][-1]['n'],1)
        for p in [[[float('nan'),0]],[[.2,.2]],[[-.1,1.1]]]:
            with self.assertRaises(ValueError):
                probability_metrics(p,['a'],labels=['a','b'])
        with self.assertRaises(ValueError):
            probability_metrics([[.5,.5]],['a'],labels=['a','b'],edges=[])

    def test_metrics_do_not_renormalize_and_change_retained_rounding(self):
        # Regression fixture for a floating-point rounding edge case.
        # A second normalization used to move reliability by one bit.
        p = np.array([[0.036568847736860303, 0.027175660095060408,
                       0.10136501108109142, 0.3233981488946984,
                       0.12763782016084477, 0.26394987646330975,
                       0.11990463556813478]])
        result = probability_metrics(p,[3],labels=tuple(range(7)))
        self.assertEqual(result['reliability'][1]['confidence'],float(p.max()))
        self.assertEqual(result['nll'],float(-np.log(p[0,3])))


class CalibrationTests(unittest.TestCase):
    def validation(self):
        return Validation(('v1','v2','v3','v4'),('a','b'),
                          [[.9,.1],[.8,.2],[.3,.7],[.4,.6]],('a','b','b','b'))

    def test_fit_rejects_test_and_ids_cannot_leak(self):
        with self.assertRaises(ValueError):
            Validation(('t',),('a','b'),[[.8,.2]],('a',),split='test')
        with self.assertRaises(ValueError):
            fit_policy({'split':'test'})
        policy = fit_policy(self.validation(),target=.8)
        with self.assertRaises(ValueError):
            evaluate_test(policy,['v1'],[[.9,.1]],{'coder':{0:'a'}})
        with self.assertRaises(ValueError):
            evaluate_test(policy,['t','t'],[[.9,.1],[.9,.1]],{})

    def test_test_answers_never_adjust_policy(self):
        policy = fit_policy(self.validation(),cutoffs=(.5,.8,.9),target=.7)
        original = (policy.temperature,policy.cutoff,policy.thresholds)
        a = evaluate_test(policy,['test'],[[.999,.001]],{'coder':{0:'a'}})
        b = evaluate_test(policy,['test'],[[.999,.001]],{'coder':{0:'b'}})
        self.assertEqual(a['n'],b['n'])
        self.assertNotEqual(a['references']['coder']['matches'],b['references']['coder']['matches'])
        self.assertEqual(original,(policy.temperature,policy.cutoff,policy.thresholds))

    def test_zero_underflow_requires_stable_logs(self):
        with self.assertRaises(ValueError):
            scaled_probabilities([[1,0]],.5)
        result = scaled_probabilities([[1,0]],.5,log_probabilities=[[0,-1000]])
        self.assertEqual(result.argmax(1).tolist(),[0])
        with self.assertRaises(ValueError):
            scaled_probabilities([[.9,.1]],.5,log_probabilities=[[-1000,0]])


class StabilityTests(unittest.TestCase):
    def test_duplicate_items_are_not_collapsed(self):
        fixture = {'signature': 'duplicate-item', 'left_count': 3, 'right_count': 0}
        left = [fixture['signature']]*fixture['left_count']
        right = [fixture['signature']]*fixture['right_count']
        multi = multiset_overlap(left,right)
        self.assertEqual(multi['left_n'],3)
        self.assertEqual(multi['removed'],left)
        self.assertEqual(overlap(left,right)['left_n'],1)
        self.assertEqual(multiset_overlap(['a','a','b'],['a','b','b'])['jaccard'],.5)
        self.assertEqual(len(multiset_stability({'a':left,'b':right,'c':[]})),3)

    def test_overlap_with_flow_projection_and_empty(self):
        result = overlap([{'span':1},{'span':2}],[{'span':2},{'span':3}],key=lambda r:r['span'])
        self.assertEqual(result['jaccard'],1/3)
        self.assertEqual(result['left_only'],1)
        self.assertIsNone(overlap([],[])['jaccard'])
        self.assertEqual(len(stability({'a':[1],'b':[1,2],'c':[]})),3)


class CompletenessTests(unittest.TestCase):
    def test_counts_every_reference_positive_and_keeps_abstention_apart(self):
        from mavc.evals import completeness
        references = {'p1': 'yes', 'p2': 'yes', 'p3': 'yes', 'p4': 'yes', 'n1': 'no'}
        predictions = {'p1': 'yes', 'p2': None, 'p3': 'no', 'n1': None}  # p4 never assessed
        report = completeness(references, predictions, is_positive=lambda v: v == 'yes')
        self.assertEqual((report['reference_positives'], report['recovered'], report['abstained'],
                          report['different'], report['missing']), (4, 1, 1, 1, 1))
        self.assertEqual(report['recall'], 0.25)
        self.assertAlmostEqual(report['abstention_rate'], 2 / 4)
        self.assertIsNone(completeness({}, {}, is_positive=bool)['recall'])



if __name__ == '__main__':
    unittest.main()
