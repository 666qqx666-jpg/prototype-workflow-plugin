import io,os,tempfile,unittest,zipfile
from pathlib import Path
from tools.import_baseline import import_baseline, archive_names
class ImportTests(unittest.TestCase):
    def test_unsafe_archives(self):
        for names in [('../bad',),('/bad',),('ok','ok'),('a/./b',),('a//b',)]:
            with self.subTest(names=names), io.BytesIO() as raw:
                with zipfile.ZipFile(raw,'w') as z:
                    for n in names:z.writestr(n,b'x')
                raw.seek(0)
                with zipfile.ZipFile(raw) as z, self.assertRaises(ValueError):list(archive_names(z))
    def test_symlink(self):
        with io.BytesIO() as raw:
            with zipfile.ZipFile(raw,'w') as z:
                info=zipfile.ZipInfo('link');info.external_attr=0o120777<<16;z.writestr(info,b'target')
            raw.seek(0)
            with zipfile.ZipFile(raw) as z,self.assertRaises(ValueError):list(archive_names(z))
    @unittest.skipUnless(os.environ.get('PROTOTYPE_BASELINE_KIT'),'private baseline not supplied')
    def test_private_import(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=import_baseline(Path(os.environ['PROTOTYPE_BASELINE_KIT']),Path(tmp)/'stage')
            self.assertFalse((root/'config').exists())
            self.assertFalse(list(root.rglob('FIXTURE_PASS.json')))
            self.assertTrue((root/'runtime/enterprise_prd/prototype/start.py').is_file())
            self.assertEqual(len(list((root/'skills').glob('*/SKILL.md'))),5)
