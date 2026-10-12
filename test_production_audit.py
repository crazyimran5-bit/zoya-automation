import unittest
import hashlib
import tempfile
from pathlib import Path
from production_audit import assess_health, assess_tool_response, hashes_match


class AuditTests(unittest.TestCase):
    def test_manifest_cannot_read_outside_selected_root(self):
        with tempfile.TemporaryDirectory() as folder:
            parent = Path(folder)
            root = parent / 'selected'
            root.mkdir()
            outside = parent / 'private'
            outside.write_bytes(b'fixture')
            digest = hashlib.sha256(b'fixture').hexdigest()
            self.assertFalse(hashes_match(root, {'../private': digest}))
            self.assertFalse(hashes_match(root, {}))
            (root / 'app.py').write_bytes(b'fixture')
            self.assertTrue(hashes_match(root, {'app.py': digest}))

    def test_stale_health_does_not_establish_a_running_microphone(self):
        result = assess_health({'heartbeat': 10, 'microphone_open': True}, now=100)
        self.assertFalse(result['heartbeat_fresh'])
        self.assertFalse(result['microphone_active'])

    def test_future_timestamp_is_not_accepted_as_fresh(self):
        self.assertFalse(assess_health({'heartbeat': 110}, now=100)['heartbeat_fresh'])

    def test_pending_approval_is_not_execution(self):
        result = assess_tool_response({'status': 'pending', 'approvalId': 'fixture'})
        self.assertTrue(result['approval_pending'])
        self.assertFalse(result['subagent_invocation_observed'])

    def test_model_prose_is_not_a_tool_result(self):
        result = assess_tool_response({'status': 'done', 'result': {
            'displayMessage': 'All tasks completed'}})
        self.assertFalse(result['subagent_invocation_observed'])

    def test_direct_tool_success_does_not_prove_all_workers_or_outcomes(self):
        result = assess_tool_response({'status': 'done', 'result': {
            'toolName': 'run_subagent', 'success': True}})
        self.assertTrue(result['subagent_invocation_observed'])
        self.assertFalse(result['business_outcome_verified'])
        self.assertFalse(result['all_workers_verified'])


if __name__ == '__main__':
    unittest.main()
