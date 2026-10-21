import fcntl,json,os,stat,tempfile
from contextlib import contextmanager
from pathlib import Path
from .contracts import WorkflowError

def payload(document):return (json.dumps(document,sort_keys=True,separators=(',',':'))+'\n').encode()
def no_symlink(path):
    if any(p.is_symlink() for p in (path,*path.parents)):raise WorkflowError('SYMLINK_PATH')
def _sync(path):
    fd=os.open(path,os.O_RDONLY)
    try:os.fsync(fd)
    finally:os.close(fd)
def _snapshot(path):
    no_symlink(path)
    try:info=path.stat()
    except FileNotFoundError:return None
    if not stat.S_ISREG(info.st_mode):raise WorkflowError('ACTIVE_POINTER_DRIFT')
    return (info.st_dev,info.st_ino,stat.S_IMODE(info.st_mode),path.read_bytes())

class Replacement:
    """Ownership-checked compensation for one atomic publication under update_lock.

    Track the staged inode BEFORE rename: rename may succeed and then fsync (or
    an interrupt) may raise. Content equality alone cannot establish ownership.
    This handles caught failures, not process kill or power loss.
    """
    def __init__(self,path):
        self.path=path;self.before=_snapshot(path);self.owned=None
    def stage(self,path,raw):
        if path!=self.path or _snapshot(path)!=self.before:
            raise WorkflowError('ACTIVE_POINTER_DRIFT')
        self.owned=_snapshot(Path(raw))
    def restore(self):
        current=_snapshot(self.path)
        if current==self.before:return
        if self.owned is None or current!=self.owned:
            raise WorkflowError('ACTIVE_POINTER_DRIFT')
        if self.before is None:
            self.path.unlink();_sync(self.path.parent);return
        fd,raw=tempfile.mkstemp(prefix='.restore-',dir=self.path.parent)
        try:
            with os.fdopen(fd,'wb') as stream:
                stream.write(self.before[3]);stream.flush();os.fsync(stream.fileno())
            os.chmod(raw,self.before[2])
            if _snapshot(self.path)!=self.owned:raise WorkflowError('ACTIVE_POINTER_DRIFT')
            os.replace(raw,self.path);_sync(self.path.parent)
        finally:
            if os.path.exists(raw):os.unlink(raw)

def write(path,document,*,replace=False,transaction=None):
    no_symlink(path);path.parent.mkdir(parents=True,exist_ok=True)
    fd,raw=tempfile.mkstemp(prefix='.write-',dir=path.parent)
    try:
        with os.fdopen(fd,'wb') as s:s.write(payload(document));s.flush();os.fsync(s.fileno())
        if replace:
            if transaction is not None:transaction.stage(path,raw)
            os.replace(raw,path)
        else:
            os.chmod(raw,0o400)
            os.link(raw,path)
        _sync(path.parent)
    finally:
        if os.path.exists(raw):os.unlink(raw)
def write_once(path,document):write(path,document)
@contextmanager
def update_lock(home):
    path=home/'.local/share/prototype-workflow/update.lock';no_symlink(path);path.parent.mkdir(parents=True,exist_ok=True)
    fd=os.open(path,os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'a+b') as stream:
        fcntl.flock(stream,fcntl.LOCK_EX)
        try:yield
        finally:fcntl.flock(stream,fcntl.LOCK_UN)
