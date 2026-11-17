"""Maintainer-only offline checks. Does not install or override production trust."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
CHILD = r'''
import hashlib,json,os,sys,unittest
from pathlib import Path
from plugin_runtime import cli,manager
candidate=Path(os.environ['TEST_PLUGIN_ROOT']).resolve()
for module in (cli,manager):
    if not Path(module.__file__).resolve().is_relative_to(candidate):
        raise RuntimeError('CANDIDATE_MODULE_NOT_LOADED')
backend=manager.Backend(Path(os.environ['TEST_HOME']),candidate)
release=backend.approved()
package=backend.prepare_package(release)
suite=unittest.TestLoader().loadTestsFromNames([
    'tests.test_refresh_cwd','tests.test_candidate_entry','tests.test_manager.ManagerTests'])
result=unittest.TextTestRunner(verbosity=2).run(suite)
report={
 'kind':'local-candidate-offline-check',
 'distribution_version':release['distribution_version'],
 'package_sha256':release['package_sha256'],
 'loaded_modules':{m.__name__:hashlib.sha256(Path(m.__file__).read_bytes()).hexdigest() for m in (cli,manager)},
 'tests':{'run':result.testsRun,'failures':len(result.failures),'errors':len(result.errors),'skipped':len(result.skipped)},
 'execution':{
   'candidate_archive_verification':'real',
   'candidate_python_module_loading':'real',
   'cli_new_resume_run_routing':'real_with_simulated_backend',
   'requirement_binding_validation':'simulated_backend_not_production_contract',
   'refresh_source_rejection':'real_decision_with_simulated_transport',
   'installer_dependency_recovery_ssh':'simulated_or_not_run',
   'desktop_native_marketplace':'not_run',
   'public_source_install':'not_run',
   'browser_runtime_audit':'not_run',
   'business_first_generation_review_gate':'not_run'},
 'status':'pass' if result.wasSuccessful() and not result.skipped else 'fail'}
Path(os.environ['TEST_REPORT']).write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
raise SystemExit(0 if report['status']=='pass' else 1)
'''


def verify(plugin_root, output):
    plugin_root = plugin_root.resolve()
    if not (plugin_root / 'plugin.json').is_file():
        raise ValueError('BUILT_PLUGIN_ROOT_REQUIRED')
    output.mkdir(parents=True, exist_ok=False)
    # Import and normal-Python bytecode checks touch only this disposable copy.
    with tempfile.TemporaryDirectory(prefix='prototype-candidate-check-') as tmp:
        isolated = Path(tmp).resolve()
        candidate = isolated / 'candidate'
        if any(p.is_symlink() for p in plugin_root.rglob('*')):
            raise ValueError('CANDIDATE_SYMLINK')
        shutil.copytree(plugin_root, candidate, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
        home = isolated / 'home'
        home.mkdir()
        env = {k: os.environ[k] for k in ('PATH', 'TMPDIR', 'LANG') if k in os.environ}
        env.update(HOME=str(home), CODEX_HOME=str(home / '.codex'),
                   XDG_CONFIG_HOME=str(home / '.config'), PYTHONNOUSERSITE='1',
                   PYTHONDONTWRITEBYTECODE='1', PYTHONPATH=os.pathsep.join((str(candidate), str(ROOT))),
                   TEST_PLUGIN_ROOT=str(candidate), TEST_HOME=str(home), TEST_REPORT=str(output / 'result.json'))
        result = subprocess.run([sys.executable, '-B', '-c', CHILD], cwd=isolated,
                                env=env, capture_output=True, text=True, timeout=180)
        (output / 'stdout.log').write_text(result.stdout)
        (output / 'stderr.log').write_text(result.stderr)
        if not (output / 'result.json').is_file():
            (output / 'result.json').write_text(json.dumps({'kind': 'local-candidate-offline-check',
                'status': 'fail', 'error': 'CHECK_PROCESS_FAILED', 'returncode': result.returncode}) + '\n')
        return result.returncode


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plugin-root', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    code = verify(args.plugin_root, args.output.resolve())
    print(json.dumps({'result': str(args.output.resolve() / 'result.json'), 'exit_code': code}))
    raise SystemExit(code)
