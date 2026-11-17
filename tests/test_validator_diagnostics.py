import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'runtime'))
from enterprise_prd.prototype.errors import GateBlockedError
from plugin_runtime.manager import Backend


class ValidatorDiagnosticsTests(unittest.TestCase):
    def test_stack_trace_retains_only_known_identifier(self):
        error = GateBlockedError.validator_failure('FAIL /private/path\nError: E_RUNTIME_STATE_RESTORE private-card drift=secret\nTOKEN=secret')
        self.assertEqual(error.validator_error, 'E_RUNTIME_STATE_RESTORE')
        self.assertEqual(str(error), 'v4 validator failed')

    def test_unknown_and_extended_tokens_not_exposed(self):
        for raw in ('secret', 'E_RUNTIME_STATE_RESTORE_SECRET', 'E_UNKNOWN_TOKEN'):
            self.assertIsNone(GateBlockedError.validator_failure(raw).validator_error)

    def test_actual_backend_failure_preserves_exit_and_redacts_details(self):
        with tempfile.TemporaryDirectory() as tmp:
            backend = Backend(Path(tmp))
            for diagnostic in ('E_RUNTIME_STATE_RESTORE', 'E_RUNTIME_STATE_RESTORE_SECRET'):
                error = GateBlockedError('private /path and business secret')
                error.validator_error = diagnostic
                def fail(*args): raise error
                kit = types.SimpleNamespace(runtime=lambda _:contextlib.nullcontext(None), dispatch_bound=fail)
                out = io.StringIO()
                with patch.object(backend,'validate_binding'), patch.object(backend,'kit',return_value=contextlib.nullcontext(kit)), contextlib.redirect_stderr(out):
                    code = backend.invoke({'runtime_root':tmp}, ['prototype','validate-output'])
                self.assertEqual(code,6)
                value=json.loads(out.getvalue())
                self.assertEqual(value['status'],'restart_required')
                self.assertEqual(value.get('validator_error'), diagnostic if diagnostic=='E_RUNTIME_STATE_RESTORE' else None)
                self.assertNotIn('private',out.getvalue())
                self.assertNotIn('secret',out.getvalue())


if __name__ == '__main__': unittest.main()
