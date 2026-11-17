"""Explicit private candidate entry. Public refresh/trust policy is unchanged."""
import argparse
import contextlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
import zipfile

from . import contracts as c, manager, storage


def selected_release(plugin_root, version, package_sha256):
    c.version(version)
    if not re.fullmatch('[0-9a-f]{64}', package_sha256):
        raise c.WorkflowError('INVALID_DIGEST')
    storage.no_symlink(plugin_root)
    release = c.release(c.read(plugin_root / 'release/approved.json'))
    manifest = c.read(plugin_root / 'plugin.json')
    archive = plugin_root / release['package_path']
    storage.no_symlink(archive)
    if (release['distribution_version'] != version or
            manifest.get('version') != version or manifest.get('name') != manager.PLUGIN):
        raise c.WorkflowError('PLUGIN_VERSION_MISMATCH')
    if release['package_sha256'] != package_sha256 or c.sha(archive) != package_sha256:
        raise c.WorkflowError('PACKAGE_MISMATCH')
    return release


def read_identity_config(path):
    storage.no_symlink(path)
    # This option accepts existing private JSON, never a key file.
    if '.ssh' in path.parts or path.suffix != '.json':
        raise c.WorkflowError('INVALID_USER_CONFIG')
    info = path.stat()
    if not path.is_file() or info.st_size > 65536 or info.st_uid != os.getuid() or info.st_mode & 0o777 != 0o600:
        raise c.WorkflowError('INVALID_USER_CONFIG')
    data = c.read(path)
    c.closed(data, {'schema_version', 'ssh_user', 'ssh_identity'})
    if not isinstance(data['ssh_user'], str) or not isinstance(data['ssh_identity'], str):
        raise c.WorkflowError('INVALID_USER_CONFIG')
    return data


def seed_dependency_cache(source, home, release, package):
    """Reuse an explicitly selected installed cache; never skip restore/smoke."""
    source = Path(source).expanduser().absolute()
    storage.no_symlink(source)
    state = c.read(source / 'state.json')
    if state.get('status') != 'installed':raise c.WorkflowError('CACHE_NOT_INSTALLED')
    old_version = state.get('version');c.version(old_version)
    lock = source / f'release/versions/{old_version}__{old_version}/skill/prototype-design-workflow/assets/developer-reader-shell/package-lock.json'
    if not lock.resolve().is_relative_to(source.resolve()):raise c.WorkflowError('CACHE_SYMLINK_ESCAPE')
    with zipfile.ZipFile(package / f"registry/releases/enterprise-prototype-skills-{release['distribution_version']}.zip") as archive:
        expected = archive.read('prototype-design-workflow/assets/developer-reader-shell/package-lock.json')
    if lock.read_bytes() != expected:raise c.WorkflowError('CACHE_LOCK_MISMATCH')
    target = home / '.local/share/enterprise-prototype' / release['distribution_version']
    npm = source / 'cache/npm'
    browsers = source / 'browsers'
    if not npm.is_dir() or not browsers.is_dir():raise c.WorkflowError('CACHE_UNAVAILABLE')
    selected = [p for p in browsers.iterdir() if p.is_dir() and
                p.name.startswith(('chromium-', 'chromium_headless_shell-', 'ffmpeg-')) and
                (p / 'INSTALLATION_COMPLETE').is_file()]
    if not selected:raise c.WorkflowError('CACHE_UNAVAILABLE')
    for path in [npm, *selected]:
        if path.is_symlink():raise c.WorkflowError('CACHE_SYMLINK_ESCAPE')
        for item in [path, *path.rglob('*')]:
            if item.is_symlink() and not item.resolve().is_relative_to(path.resolve()):
                raise c.WorkflowError('CACHE_SYMLINK_ESCAPE')
    shutil.copytree(npm, target / 'cache/npm', symlinks=True)
    for path in selected:shutil.copytree(path, target / 'browsers' / path.name, symlinks=True)
    return {'used': True, 'cold_install': False, 'browser_directories': [p.name for p in selected]}


@contextlib.contextmanager
def isolated_process(home):
    # Installer and children use only this new HOME; the OS owner home remains
    # available to the existing key-path/metadata validator via getpwuid().
    updates = {'HOME': str(home), 'CODEX_HOME': str(home / '.codex'),
               'XDG_CONFIG_HOME': str(home / '.config'), 'PYTHONDONTWRITEBYTECODE': '1',
               'PYTHONNOUSERSITE': '1', 'npm_config_userconfig': os.devnull,
               'npm_config_audit': 'false', 'npm_config_fund': 'false',
               'npm_config_update_notifier': 'false'}
    previous = {key: os.environ.get(key) for key in updates}
    cwd = Path.cwd()
    os.environ.update(updates)
    os.chdir(home)
    try:
        yield
    finally:
        os.chdir(cwd)
        for key, value in previous.items():
            if value is None:os.environ.pop(key, None)
            else:os.environ[key] = value


def start_trial(*, plugin_root, trial_root, version, package_sha256, company_profile, user_config, dependency_cache=None):
    plugin_root = Path(plugin_root).expanduser().absolute()
    trial_root = Path(trial_root).expanduser().absolute()
    company_profile = Path(company_profile).expanduser().absolute()
    user_config = Path(user_config).expanduser().absolute()
    storage.no_symlink(trial_root)
    if trial_root.exists():raise c.WorkflowError('TRIAL_ROOT_EXISTS')
    if not trial_root.parent.is_dir():raise c.WorkflowError('TRIAL_PARENT_REQUIRED')
    if trial_root.is_relative_to(plugin_root):raise c.WorkflowError('TRIAL_INSIDE_PLUGIN')
    release = selected_release(plugin_root, version, package_sha256)
    identity = read_identity_config(user_config)
    # Atomic, exclusive allocation: never accept a caller's populated HOME.
    trial_root.mkdir(mode=0o700)
    home, workspace = trial_root / 'home', trial_root / 'workspace'
    home.mkdir(mode=0o700)
    binding = None
    result = None
    backend = None
    try:
        storage.write_once(trial_root / 'candidate-selection.json', {
            'mode': 'private-candidate', 'plugin_root': str(plugin_root), 'release': release})
        with isolated_process(home):
            backend = manager.Backend(home, plugin_root)
            if backend.approved() != release:raise c.WorkflowError('CANDIDATE_CHANGED')
            cache = {'used': False, 'cold_install': True}
            if dependency_cache is not None:
                package = backend.prepare_package(release)
                cache = seed_dependency_cache(dependency_cache, home, release, package)
            manager.setup(home=home, plugin_root=plugin_root, company_profile=company_profile,
                          ssh_user=identity['ssh_user'], ssh_identity=identity['ssh_identity'], backend=backend,
                          expected_release=release)
            with backend.update_lock(home):
                if selected_release(plugin_root, version, package_sha256) != release:
                    raise c.WorkflowError('CANDIDATE_CHANGED')
                binding = manager.bind_requirement(workspace=workspace, release=release, backend=backend)
                if selected_release(plugin_root, version, package_sha256) != release:
                    raise c.WorkflowError('CANDIDATE_CHANGED')
                backend.verify_requirement(workspace / '.prototype-workflow/requirement.json')
                result = {'status': 'ready', 'mode': 'private-candidate',
                          'distribution_version': version, 'home': str(home),
                          'workspace': str(workspace), 'binding': binding,
                          'dependency_cache': cache,
                          'public_source_updated': False, 'desktop_marketplace_install': False}
                storage.write_once(trial_root / 'trial-result.json', result)
        return result
    except BaseException as error:
        if binding is not None and backend is not None:
            backend.remove_own_binding(workspace / '.prototype-workflow/requirement.json', binding)
        result_path = trial_root / 'trial-result.json'
        # Remove only this invocation's matching success bytes after a failed
        # publication (e.g. fsync interruption); keep all other failure evidence.
        if result is not None and result_path.is_file():
            if result_path.read_bytes() == storage.payload(result):result_path.unlink()
        code = str(error) if isinstance(error, c.WorkflowError) else getattr(error, 'error', type(error).__name__)
        storage.write_once(trial_root / 'trial-error.json', {
            'status': getattr(error, 'status', 'blocked'), 'error': code,
            'distribution_version': version, 'mode': 'private-candidate'})
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for flag in ('plugin-root', 'trial-root', 'company-profile', 'user-config'):
        parser.add_argument('--' + flag, type=Path, required=True)
    parser.add_argument('--version', required=True)
    parser.add_argument('--package-sha256', required=True)
    parser.add_argument('--dependency-cache', type=Path, help='已有成功安装目录；锁文件必须相同，仍完整执行恢复和 Smoke')
    args = parser.parse_args(argv)
    try:
        result = start_trial(**vars(args))
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except Exception as error:
        code = str(error) if isinstance(error, c.WorkflowError) else getattr(error, 'error', type(error).__name__)
        print(json.dumps({'status': getattr(error, 'status', 'blocked'), 'error': code,
                          'next_step': '保留隔离目录和错误记录；修正后使用新目录重试'}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
