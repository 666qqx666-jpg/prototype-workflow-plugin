import io,json,tempfile,unittest,zipfile
from pathlib import Path
from tools.audit_publication import audit_bytes,audit_tree
from tools.build_public_plugin import build
class PublicationTests(unittest.TestCase):
    def test_nested_archive_rejected(self):
        b=io.BytesIO()
        with zipfile.ZipFile(b,'w') as z:z.writestr('inside.txt',b'BEGIN '+b'OPENSSH PRIVATE KEY')
        self.assertTrue(audit_bytes('a.zip',b.getvalue(),[]))
    def test_private_values_and_path_rejected(self):
        self.assertTrue(audit_bytes('file.txt',b'private-service',['private-service']))
        self.assertTrue(audit_bytes('file.txt',b'/Users/'+b'person/secret',[]))
    def test_deterministic_and_exclusive(self):
        with tempfile.TemporaryDirectory() as tmp:
            a=build('0.2.0',Path(tmp)/'a');b=build('0.2.0',Path(tmp)/'b')
            self.assertEqual(a,b)
            with self.assertRaises(FileExistsError):build('0.2.0',Path(tmp)/'a')

class HistoryTests(unittest.TestCase):
    def test_removed_secret_still_rejected_in_history(self):
        import subprocess
        from tools.audit_publication import audit_history
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            def git(*args):return subprocess.run(['git','-c','user.name=Fixture','-c','user.email=fixture@example.invalid','-c','commit.gpgsign=false',*args],cwd=root,check=True,capture_output=True)
            git('init');(root/'removed.txt').write_text('synthetic-denied-value');git('add','.');git('commit','-m','fixture');git('rm','removed.txt');git('commit','-m','remove')
            self.assertTrue(audit_history(root,['synthetic-denied-value']))
