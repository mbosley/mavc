import json
from pathlib import Path
import tempfile
import time
import unittest

from mavc.tools import Tool, ToolInput, ToolRejection, ToolSession
from mavc.describe import describe
from mavc import Operation, Runner
from test_model import response


class Candidate(ToolInput):
    valid: bool


def compile_candidate(session, input):
    if not input.valid:
        raise ToolRejection(ValueError('accepted graph edit node type differs'))
    return {'unchanged': input.valid}


class ToolTests(unittest.TestCase):
    def session(self):
        self.rows = []
        tool = Tool('finish', Candidate, compile_candidate, terminal=True)
        return ToolSession([tool], {}, record=self.rows.append)

    def call(self, session, id, input, phase='invoke'):
        return session.dispatch({'id': id, 'name': 'finish', 'input': input, 'phase': phase})

    def test_exact_feedback_correction_and_static_description(self):
        session = self.session()
        manifest = session.tools['finish'].describe()
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'unexecuted.json'
            path.write_text(json.dumps({'flow_id': 'fixture', 'positions': {'Model': {
                'tool_manifest': [manifest]}}}))
            self.assertIn('"valid"', describe(path))
            self.assertEqual(session.submissions, 0)
        self.call(session, 'a', {'valid': False}, 'begin')
        bad = self.call(session, 'a', {'valid': False})
        self.assertEqual(bad['error'], {'class': 'ValueError', 'message': 'accepted graph edit node type differs'})
        good = self.call(session, 'b', {'valid': True})
        self.assertEqual(good['result'], {'unchanged': True})
        self.assertEqual(good['correction_count'], 1)
        self.assertTrue(good['closed'])
        self.assertEqual(len(session.calls), 2)

    def test_schema_rejections_close_on_third_without_fourth(self):
        session = self.session()
        for index in range(3):
            result = self.call(session, str(index), {'valid': 'invalid'}, 'sdk_rejected')
            self.assertEqual(result['error']['class'], 'ValidationError')
            self.assertEqual(result['correction_count'], index)
        self.assertTrue(result['closed'])
        with self.assertRaises(TimeoutError):
            self.call(session, 'fourth', {'valid': True})
        self.assertEqual(len(session.calls), 3)

    def test_no_late_commit_and_infrastructure_does_not_spend_correction(self):
        state = []
        def mutate(session, input):
            session.deadline = time.monotonic() - 1
            session.check_live()
            state.append(input.valid)
        rows = []
        session = ToolSession([Tool('finish', Candidate, mutate, terminal=True)], {}, record=rows.append)
        result = self.call(session, 'expired', {'valid': True})
        self.assertTrue(result['fatal'])
        self.assertEqual(session.submissions, 0)
        session = self.session()
        self.call(session, 'cancelled', {'valid': True}, 'begin')
        result = session.dispatch({'id': 'cancelled', 'name': 'finish', 'input': {'valid': True},
            'phase': 'invoke', 'deadline_monotonic': time.monotonic() + 10, 'cancelled': lambda: True})
        self.assertTrue(result['fatal'])
        self.assertEqual(session.submissions, 0)
        self.assertEqual(state, [])
        session = self.session()
        self.call(session, 'late', {'valid': True}, 'begin')
        result = session.dispatch({'id': 'late', 'name': 'finish', 'input': {'valid': True},
            'phase': 'invoke', 'deadline_monotonic': time.monotonic() - 1})
        self.assertTrue(result['fatal'])
        self.assertEqual(session.submissions, 0)

    def test_legacy_parent_observation_does_not_count_nested_submission_twice(self):
        rows = []
        tools = [Tool('finish', None, schema={'type': 'object', 'additionalProperties': False},
            terminal=True, legacy_submit='submit'),
            Tool('submit', Candidate, compile_candidate, terminal=True, active=False)]
        session = ToolSession(tools, {}, record=rows.append)
        parent = {'id': 'parent', 'name': 'finish', 'input': {}, 'phase': 'begin'}
        session.dispatch(parent)
        child = session.dispatch({'id': 'parent/1', 'parent_id': 'parent', 'name': 'submit',
            'input': {'valid': True}, 'phase': 'invoke', 'deadline_monotonic': time.monotonic() + 10})
        observed = session.dispatch({**parent, 'phase': 'observe_result',
            'result': {'details': {'accepted': True, 'proposal': child['result']}}})
        self.assertFalse(observed['terminal'])
        self.assertEqual(session.submissions, 1)
        self.assertEqual(session.calls['parent/1']['parent_id'], 'parent')

    def test_bound_model_leaf_retains_nested_receipts(self):
        tool = Tool('finish', Candidate, compile_candidate, terminal=True)
        class Bound(Operation):
            Input = Output = Candidate
            def bind_tools(self, input, request, children):
                return [tool], {}
            def bind_caller(self, dispatch):
                def call(request):
                    result = dispatch({'id': 'native-1', 'name': 'finish', 'input': {'valid': True}, 'phase': 'invoke'})
                    value = response(request)
                    value.update(proposal={'valid': result['result']['unchanged']}, text='acknowledged')
                    value['observed']['active_tools'] = ['finish']
                    value['actual_requests'][0]['tools'] = [{'type': 'function', 'name': 'finish'}]
                    value['observed']['request']['tools'] = value['actual_requests'][0]['tools']
                    return value
                return call
        settings = {'provider': 'fixture', 'model': 'fixture-model', 'reasoning': 'medium',
            'settings': {}, 'tools': ['finish'], 'terminal_tool': 'finish', 'tool_manifest': [tool.describe()]}
        with tempfile.TemporaryDirectory() as folder:
            with Runner(folder, {'flow_id': 'tools-fixture', 'positions': {'Bound': {'legacy': settings}}}) as run:
                result = Bound(caller=lambda request: None)(run, {'valid': True})
                self.assertTrue(result.valid)
                node = run.index()['Bound']
                self.assertEqual(node.details['tool_calls']['native-1']['outcome'], 'accepted')
                self.assertEqual(node.declaration['operation']['tools'], [tool.describe()])

    def test_actual_session_later_execution_error_invalidates_acceptance(self):
        session = self.session()
        accepted = self.call(session, 'accepted', {'valid': True})
        ordinary = session.dispatch({'id': 'accepted', 'name': 'finish', 'input': {'valid': True},
            'phase': 'observe_result', 'isError': False, 'result': {'details': accepted}})
        self.assertEqual(ordinary, accepted)
        failure = session.dispatch({'id': 'accepted', 'name': 'finish', 'input': {'valid': True},
            'phase': 'observe_result', 'isError': True,
            'result': {'content': [{'type': 'text', 'text': 'post-execute SDK failure'}], 'details': accepted}})
        self.assertTrue(failure['fatal'])
        self.assertIsNone(failure['result'])
        self.assertEqual(failure['error'], {'class': 'ToolExecutionError', 'message': 'post-execute SDK failure'})
        self.assertEqual(session.calls['accepted']['execution_error_result']['details'], accepted)
        self.assertEqual(session.submissions, 1)
        legacy = Tool('legacy', None, schema={'type': 'object'}, terminal=False)
        session = ToolSession([legacy], {}, record=lambda row: None)
        begin = {'id': 'draft', 'name': 'legacy', 'input': {}, 'phase': 'begin'}
        session.dispatch(begin)
        rejection = session.dispatch({**begin, 'phase': 'observe_result', 'isError': True,
            'result': {'content': [{'type': 'text', 'text': 'native draft rejection'}], 'details': {'accepted': False}}})
        self.assertFalse(rejection['fatal'])
        self.assertFalse(rejection['closed'])
        failure = session.dispatch({**begin, 'phase': 'observe_result', 'isError': True,
            'result': {'content': [{'type': 'text', 'text': 'legacy threw'}], 'details': {}}})
        self.assertTrue(failure['fatal'])
        self.assertEqual(session.submissions, 0)
        tools = [Tool('parent', None, schema={'type': 'object'}, terminal=True, legacy_submit='finish'),
            Tool('finish', Candidate, compile_candidate, terminal=True, active=False)]
        session = ToolSession(tools, {}, record=lambda row: None)
        for index in range(3):
            parent = {'id': str(index), 'name': 'parent', 'input': {}, 'phase': 'begin'}
            session.dispatch(parent)
            frame = {'id': str(index) + '/1', 'parent_id': str(index),
                'name': 'finish', 'input': {'valid': False}, 'phase': 'invoke'}
            child = session.dispatch(frame)
            observed = session.dispatch({**frame, 'phase': 'observe_result', 'isError': True,
                'result': {'details': child}})
            self.assertEqual(observed, child)
            mirrored = session.dispatch({**parent, 'phase': 'observe_result', 'isError': True,
                'result': {'details': child}})
            self.assertFalse(mirrored['fatal'])
            self.assertFalse(mirrored['terminal'])
            self.assertEqual(session.submissions, index + 1)
            self.assertEqual(mirrored['closed'], index == 2)


if __name__ == '__main__':
    unittest.main()
