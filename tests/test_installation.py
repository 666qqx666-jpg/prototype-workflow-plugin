import importlib.util,sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'bootstrap'))
spec=importlib.util.spec_from_file_location('public_kit',Path(__file__).resolve().parents[1]/'bootstrap/prototype-kit.py');kit=importlib.util.module_from_spec(spec);spec.loader.exec_module(kit)
class BoundDispatchTests(unittest.TestCase):
    def test_rejects_non_prototype_after_verify(self):
        calls=[]
        def verify(b):calls.append('verify');return b
        def invoke(root,args):calls.append('invoke');return 0
        with self.assertRaisesRegex(ValueError,'PROTOTYPE_ONLY'):kit.dispatch_bound({'runtime_root':'synthetic'},['prd','start'],verify,invoke)
        self.assertEqual(calls,['verify'])
    def test_invalid_binding_never_invokes(self):
        def reject(b):raise ValueError('BAD_BINDING')
        with self.assertRaisesRegex(ValueError,'BAD_BINDING'):kit.dispatch_bound({},['prototype','gate'],reject,lambda *x:self.fail('invoked'))
    def test_bound_dispatch_calls_exact_root(self):
        self.assertEqual(kit.dispatch_bound({'runtime_root':'old'},['prototype','gate'],lambda b:b,lambda r,a:(r,a)),('old',['prototype','gate']))
    def test_receipt_schemas_and_smoke_parameter(self):
        import inspect
        self.assertIn('company_profile_sha256',kit.ACTIVATION_KEYS);self.assertIn('company_profile_sha256',kit.QUERY_RECEIPT_KEYS)
        self.assertIn('installed_smoke =',inspect.getsource(kit.doctor));self.assertNotIn('    smoke =',inspect.getsource(kit.doctor))
