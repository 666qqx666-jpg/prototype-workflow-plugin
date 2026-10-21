import json,subprocess,sys,unittest
from pathlib import Path
class PluginTests(unittest.TestCase):
    def test_unknown_command_rejected(self):
        p=subprocess.run([sys.executable,'-B','-m','plugin_runtime.cli','exec'],capture_output=True,text=True);self.assertNotEqual(p.returncode,0)
    def test_entry_reads_bound_skills(self):
        text=Path('skills/prototype-workflow-entry/SKILL.md').read_text()
        for token in ('loaded-plugin-version=0.2.0','binding.start_here_path','resume','独立 Review','run','不自动升级'):self.assertIn(token,text)
    def test_setup_uses_existing_identity(self):
        text=Path('skills/prototype-setup/SKILL.md').read_text();self.assertIn('不索要私钥正文',text);self.assertIn('ready',text)

class ErrorRecordTests(unittest.TestCase):
    def test_cli_failure_creates_redacted_local_record(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            home=Path(tmp).resolve()
            p=subprocess.run([sys.executable,'-B','-m','plugin_runtime.cli','resume','--home',str(home),'--workspace',str(home/'missing')],capture_output=True,text=True)
            self.assertNotEqual(p.returncode,0);result=json.loads(p.stderr.splitlines()[-1]);record=Path(result['record_path'])
            self.assertTrue(record.is_relative_to(home));self.assertEqual(set(json.loads(record.read_text())),{'status','error','distribution_version'})
            self.assertNotIn(str(home),record.read_text())
