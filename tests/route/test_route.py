from dataclasses import asdict
import json
import unittest
from mavc.route import Prediction, RouteFailure, route


class RouteTests(unittest.TestCase):
    def run_route(self, prediction, **kwargs):
        return route('action-17', {'text': 'original'}, local=lambda _: prediction,
                     frontier=kwargs.pop('frontier', lambda _: 'frontier answer'),
                     cutoff=.9, local_model='head-fixed', frontier_model='frontier-fixed', **kwargs)

    def test_cutoff_boundary_and_confident_local_do_not_call_frontier(self):
        for confidence in [.9, .99]:
            with self.subTest(confidence=confidence):
                def forbidden(_):
                    self.fail('frontier called for selected local prediction')
                result = self.run_route(Prediction('local answer', confidence), frontier=forbidden)
                self.assertEqual((result.answer, result.route, result.model), ('local answer', 'local', 'head-fixed'))
                self.assertEqual(result.confidence, confidence)
                self.assertIsNone(result.fallback_reason)

    def test_abstention_invalid_output_and_low_confidence_fall_back_once(self):
        cases = [(Prediction('local', .899), 'low_confidence'), (Prediction(), 'abstention'),
                 (Prediction('local', .99, True), 'abstention'), ('malformed', 'invalid_output'),
                 (Prediction('unknown', .99), 'invalid_output')]
        for prediction, reason in cases:
            with self.subTest(reason=reason):
                calls = []
                result = self.run_route(prediction, valid=lambda v: v in ['local', 'frontier answer'],
                                        frontier=lambda value: calls.append(value) or 'frontier answer')
                self.assertEqual(calls, [{'text': 'original'}])
                self.assertEqual((result.answer, result.route, result.model, result.fallback_reason),
                                 ('frontier answer', 'frontier', 'frontier-fixed', reason))

    def test_invalid_confidence_is_retained_as_valid_json(self):
        for confidence in [float('nan'), float('inf'), -float('inf'), -.1, 1.1, None, 'bad', True]:
            with self.subTest(confidence=confidence):
                result = self.run_route(Prediction('local', confidence))
                self.assertEqual(result.fallback_reason, 'invalid_confidence')
                self.assertIsNone(result.confidence)
                self.assertIsNone(json.loads(json.dumps(asdict(result), allow_nan=False))['confidence'])

    def test_local_exception_and_invalidity_do_not_hide_frontier_failure(self):
        def failed_local(_):
            raise RuntimeError('local unavailable')
        calls = []
        result = route('id', 'input', local=failed_local, frontier=lambda x: calls.append(x) or 'ok',
                       cutoff=.9, local_model='local', frontier_model='frontier')
        self.assertEqual(calls, ['input'])
        self.assertEqual(result.fallback_reason, 'local_failure')
        self.assertIn('local unavailable', result.local_error)
        for frontier in [lambda _: None, lambda _: 1/0]:
            with self.subTest(frontier=frontier):
                with self.assertRaises(RouteFailure) as failure:
                    self.run_route(Prediction('tempting local answer', .1), frontier=frontier)
                failed = failure.exception.result
                self.assertEqual((failed.status, failed.route, failed.model), ('failed', 'frontier', 'frontier-fixed'))
                self.assertIsNone(failed.answer)
                self.assertEqual(failed.fallback_reason, 'low_confidence')
                self.assertIsNotNone(failure.exception.__cause__)
                self.assertEqual(json.loads(json.dumps(asdict(failed), allow_nan=False))['action_id'], 'action-17')

    def test_bad_fixed_cutoff_never_invokes_either_caller(self):
        def forbidden(_):
            self.fail('called before rejecting invalid cutoff')
        for cutoff in [float('nan'), float('inf'), -.1, 1.1, None, 'bad', True]:
            with self.subTest(cutoff=cutoff):
                with self.assertRaises(ValueError):
                    route('id', 'input', local=forbidden, frontier=forbidden,
                          cutoff=cutoff, local_model='local', frontier_model='frontier')

    def test_action_identity_and_selected_model_record_round_trip(self):
        local = self.run_route(Prediction({'units': []}, .95))
        fallback = self.run_route(Prediction('local', .4))
        records = [json.loads(json.dumps(asdict(result), allow_nan=False)) for result in [local, fallback]]
        self.assertEqual([r['action_id'] for r in records], ['action-17']*2)
        self.assertEqual([r['model'] for r in records], ['head-fixed', 'frontier-fixed'])
        self.assertEqual([r['cutoff'] for r in records], [.9]*2)
        self.assertEqual([r['status'] for r in records], ['completed']*2)
