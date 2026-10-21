"""Scan actual release bytes and every reachable Git blob; report no secret values."""
import argparse,io,json,re,subprocess,zipfile
from pathlib import Path,PurePosixPath
FORBIDDEN_PARTS={'.ssh','node_modules','__pycache__','private','.local-trials','config','Evidence'}
def audit_bytes(name,data,private_patterns,depth=0):
    hits=[]
    if depth>5:return [{'category':'archive_depth','path':name}]
    if FORBIDDEN_PARTS.intersection(PurePosixPath(name).parts) or PurePosixPath(name).name in ('known_hosts','company-profile.json','FIXTURE'+'_PASS.json','MAINTAINER'+'-RUNBOOK.md'):hits.append({'category':'private_file','path':name})
    if zipfile.is_zipfile(io.BytesIO(data)):
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                seen=set()
                for i in z.infolist():
                    if (i.external_attr>>16)&0o170000==0o120000 or i.filename in seen or '..' in PurePosixPath(i.filename).parts or i.filename.startswith('/') or i.file_size>100*1024*1024:raise ValueError()
                    seen.add(i.filename);hits+=audit_bytes(name+'!/'+i.filename,z.read(i),private_patterns,depth+1)
        except Exception:hits.append({'category':'unsafe_archive','path':name})
        return hits
    checks=[('personal_path',rb'/Users/'+rb'[A-Za-z0-9_.-]+/'),('private_key',rb'-----BEGIN '+rb'(?:OPENSSH|RSA|EC|DSA|ENCRYPTED) PRIVATE KEY-----')]
    # Also detect a plain PEM header without punctuation in malformed samples.
    checks.append(('private_key',b'BEGIN '+b'OPENSSH PRIVATE KEY'))
    for label,pattern in checks:
        if re.search(pattern,data):hits.append({'category':label,'path':name})
    for value in private_patterns:
        if value.encode() in data:hits.append({'category':'private_value','path':name});break
    return hits

def audit_tree(root,private_patterns):
    hits=[]
    for p in sorted(root.rglob('*')):
        if '.git' in p.relative_to(root).parts:continue
        if p.is_symlink():hits.append({'category':'symlink','path':str(p.relative_to(root))})
        elif p.is_file():hits+=audit_bytes(str(p.relative_to(root)),p.read_bytes(),private_patterns)
    return hits

def audit_history(repo,patterns):
    records=subprocess.check_output(['git','rev-list','--objects','--all'],cwd=repo,text=True).splitlines();hits=[]
    for line in records:
        obj,_,name=line.partition(' ')
        if subprocess.check_output(['git','cat-file','-t',obj],cwd=repo,text=True).strip()=='blob':hits+=audit_bytes('history/'+name,subprocess.check_output(['git','cat-file','blob',obj],cwd=repo),patterns)
    return hits

def main():
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);p.add_argument('--history',action='store_true');p.add_argument('--private-patterns',type=Path);a=p.parse_args()
    patterns=json.loads(a.private_patterns.read_text()) if a.private_patterns else []
    hits=audit_tree(a.root,patterns)+(audit_history(Path.cwd(),patterns) if a.history else [])
    print(json.dumps({'status':'fail' if hits else 'pass','hits':hits},ensure_ascii=False));return bool(hits)
if __name__=='__main__':raise SystemExit(main())
