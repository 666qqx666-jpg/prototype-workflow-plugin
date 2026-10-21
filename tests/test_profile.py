import base64,copy,hashlib,json,tempfile,unittest
from pathlib import Path
from bootstrap.company_profile import load_profile,import_profile,capture_profile

def profile(host='192.0.2.10',script='/srv/query.js'):
    blob=b'\x00\x00\x00\x0bssh-ed25519\x00\x00\x00\x20'+b'k'*32
    known=host+' ssh-ed25519 '+base64.b64encode(blob).decode()+'\n'
    return {'schema_version':1,'service':{'schema_version':2,'host':host,'port':22,'remote_script':script,'known_hosts_sha256':hashlib.sha256(known.encode()).hexdigest()},'known_hosts':known}
class ProfileTests(unittest.TestCase):
    def setUp(self):self.tmp=tempfile.TemporaryDirectory();self.home=Path(self.tmp.name).resolve();self.file=self.home/'input.json'
    def tearDown(self):self.tmp.cleanup()
    def save(self,p):self.file.write_text(json.dumps(p));return self.file
    def test_valid_and_no_replace(self):
        for host,script in [('192.0.2.10','/srv/query.js'),('198.51.100.5','/opt/team/q.js')]:self.assertEqual(load_profile(self.save(profile(host,script)))['service']['host'],host)
        original=profile();import_profile(self.home,self.save(original));a=capture_profile(self.home)
        import_profile(self.home,self.file);self.assertEqual(a,capture_profile(self.home))
        self.assertEqual((self.home/'.config/prototype-workflow/company/profile.json').stat().st_mode&0o777,0o400)
        with self.assertRaises(ValueError):import_profile(self.home,self.save(profile('192.0.2.11')))
    def test_rejects_malformed(self):
        cases=[]
        for k,v in [('port',23),('host','example.com'),('remote_script','/tmp/../x.js'),('remote_script','/tmp/x.js;id'),('remote_script','/a//b.js'),('schema_version',True)]:
            p=profile();p['service'][k]=v;cases.append(p)
        p=profile();p['unknown']=1;cases.append(p)
        p=profile();p['known_hosts']+='\n';cases.append(p)
        p=profile();p['service']['known_hosts_sha256']='0'*64;cases.append(p)
        for p in cases:
            with self.subTest(p=p),self.assertRaises(ValueError):load_profile(self.save(p))
        self.file.write_text('{"schema_version":1,"schema_version":1}')
        with self.assertRaises(ValueError):load_profile(self.file)
    def test_drift_and_symlink(self):
        import_profile(self.home,self.save(profile()))
        file=self.home/'.config/prototype-workflow/company/service.json';file.chmod(0o600);file.write_text('{}')
        with self.assertRaises(ValueError):capture_profile(self.home)
