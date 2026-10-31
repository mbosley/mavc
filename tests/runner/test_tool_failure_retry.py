import tempfile
import unittest
from mavc import Runner
from mavc.runner import ToolFailure, RetryableFailure, NeedsHumanReview
from mavc.declare import call_attempts


class ToolRecoveryTests(unittest.TestCase):
    def config(self):
        return {'flow_id': 'tool-recovery', 'nodes': {'reader': {'maximum_attempts': 2,
                'retry_on': 'tool_failure', 'provider_retry': {'max_retries': 4}}}}

    def test_one_typed_rerun_and_no_provider_failure_rerun(self):
        with tempfile.TemporaryDirectory() as folder:
            with Runner(folder, self.config()) as runner:
                calls = []
                def read():
                    calls.append(1)
                    if len(calls) == 1: raise ToolFailure('all edits failed')
                    return {'accepted': True}
                self.assertEqual(runner.step('r', {}, read, kind='reader', retry=True), {'accepted': True})
                self.assertEqual(runner.record.nodes['r'].attempts, 2)
                self.assertEqual(len(runner.record.nodes['r'].errors), 1)
        with tempfile.TemporaryDirectory() as folder:
            with Runner(folder, self.config()) as runner:
                def provider(): raise RetryableFailure('not a tool failure')
                with self.assertRaises(RetryableFailure): runner.step('r', {}, provider, kind='reader', retry=True)
                self.assertEqual(runner.record.nodes['r'].attempts, 1)
        with self.assertRaises(ValueError): call_attempts({'maximum_attempts': 2, 'provider_retry': {'max_retries': 4}})

    def test_second_technical_failure_is_needs_person(self):
        with tempfile.TemporaryDirectory() as folder:
            with Runner(folder, self.config()) as runner:
                def failed():
                    if runner.record.nodes['r'].attempts == 1: raise ToolFailure('tool failed')
                    raise NeedsHumanReview('technical failure after one rerun')
                with self.assertRaises(NeedsHumanReview): runner.step('r', {}, failed, kind='reader', retry=True)
                self.assertEqual(runner.record.nodes['r'].attempts, 2)
                self.assertEqual(runner.record.nodes['r'].outcome.status, 'needs_human_review')
