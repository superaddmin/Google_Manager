import contextlib
import io
import json
import unittest
from unittest.mock import patch

from deploy.recovery_drill import main, run_drill


class RecoveryDrillTests(unittest.TestCase):
    def test_shipped_cli_round_trip_and_failure_guards(self):
        result = run_drill()
        self.assertEqual(result['status'], 'passed')
        self.assertEqual(result['migrations_applied'], 5)
        self.assertTrue(result['ciphertext_readable'])
        self.assertTrue(result['wrong_key_rejected'])
        self.assertTrue(result['overwrite_rejected'])
        self.assertTrue(result['source_unchanged'])
        self.assertFalse(result['production_rpo_rto_validated'])

    def test_failure_is_nonzero_and_redacted(self):
        output = io.StringIO()
        with patch('deploy.recovery_drill.run_drill', side_effect=RuntimeError('private-key-value')):
            with contextlib.redirect_stdout(output):
                self.assertEqual(main(), 1)
        self.assertEqual(json.loads(output.getvalue())['status'], 'failed')
        self.assertNotIn('private-key-value', output.getvalue())
