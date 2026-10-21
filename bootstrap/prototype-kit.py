#!/usr/bin/env python3
"""Version-pinned, stdlib bootstrap. Never changes the frozen Candidate archives."""
from __future__ import annotations

import argparse
import base64
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import importlib
import json
import os
import pwd
from pathlib import Path, PurePosixPath
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import uuid
import zipfile

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
import company_profile
VERSION = "0.2.0"
SKILLS = ("prototype-design-workflow", "enterprise-prototype-chain",
          "enterprise-prototype-builder", "enterprise-prototype-review", "enterprise-prototype-code-query")
ACTIVATION_KEYS = {"schema_version", "version", "home", "skills", "links", "dependency", "smoke",
                   "link_receipt", "link_receipt_sha256", "code_query_receipt", "code_query_receipt_sha256", "activated_at", "company_profile_sha256"}
STATE_KEYS = {"status", "version", "activation_receipt", "activation_receipt_sha256",
              "launcher_sha256", "code_query_launcher_sha256"}
QUERY_RECEIPT_KEYS = {"schema_version", "version", "adapter_sha256", "service_config_sha256", "known_hosts_sha256",
                      "identity_sha256", "scope", "checks", "checked_at", "host_fingerprint",
                      "adapter_version", "service_config_schema_version", "user_config_sha256", "user_config_metadata_sha256", "company_profile_sha256"}
SKILL_ZIP = f"registry/releases/enterprise-prototype-skills-{VERSION}.zip"
PACK_ZIP = f"registry/releases/company-prototype-{VERSION}.zip"
FROZEN = {}  # Filled by deterministic release builder.


class KitError(RuntimeError):
    pass


class GateError(KitError):
    def __init__(self, status, error, message, next_step):
        super().__init__(message)
        self.status, self.error, self.next_step = status, error, next_step


QUERY_GUIDES = {
    "configuration_required": ("个人 SSH 配置缺失或不合法", "运行 prototype-kit.py configure-code-query"),
    "authorization_required": ("中心机拒绝当前个人 SSH 身份", "先核对已有用户名、密钥和 ssh-agent；确实未授权时联系权限负责人"),
    "host_identity_blocked": ("中心机主机身份不符合冻结配置", "联系候选包维护者通过可信渠道核实主机公钥"),
    "service_unavailable": ("代码查询服务不可用", "联系代码查询服务维护者后重试"),
    "query_probe_blocked": ("查询结果未通过校验", "检查服务结果或当前任务项目权限"),
}
QUERY_ERROR_CODES = {"invalid_configuration", "invalid_service_config", "known_hosts_mismatch", "invalid_user_config",
    "identity_invalid", "host_key_verification_failed", "authentication_failed", "query_service_failed", "spawn_failed",
    "ssh_missing", "query_output_unavailable", "invalid_list_output", "invalid_query_output", "query_result_rejected",
    "empty_query_output", "unsafe_known_hosts_path", "internal_error", "probe_configuration_changed"}


def query_error(status, error):
    return GateError(status, error, *QUERY_GUIDES[status])


def _reject_symlink_path(file, boundary):
    for item in [file, *file.parents]:
        if item.is_symlink():
            raise query_error("configuration_required", "configuration_symlink")
        if item == boundary:
            break


def code_query_user_config(home):
    return home.resolve() / ".config/prototype-workflow/user.json"


def _validated_identity(ssh_user, ssh_identity, *, identity_home=None):
    if not isinstance(ssh_user, str) or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", ssh_user) is None:
        raise query_error("configuration_required", "invalid_ssh_user")
    owner_home = (identity_home or Path(pwd.getpwuid(os.getuid()).pw_dir)).resolve()
    if not isinstance(ssh_identity, str) or re.search(r"[\x00-\x1f\x7f]", ssh_identity):
        raise query_error("configuration_required", "identity_invalid")
    raw = Path(ssh_identity)
    candidate = owner_home / ssh_identity[2:] if ssh_identity.startswith("~/") else raw
    try:
        if not candidate.is_absolute() or candidate.is_symlink():
            raise ValueError()
        candidate.lstat()  # reject final symlink before realpath; do not open the key
        identity = candidate.resolve(strict=True)
        identity.relative_to(owner_home / ".ssh")
        stat = identity.stat()
        if not identity.is_file() or stat.st_mode & 0o077 or stat.st_uid != os.getuid():
            raise ValueError()
    except (OSError, ValueError):
        raise query_error("configuration_required", "identity_invalid") from None
    return identity


def configure_code_query(home, ssh_user, ssh_identity, *, identity_home=None):
    identity = _validated_identity(ssh_user, ssh_identity, identity_home=identity_home)
    output = code_query_user_config(home)
    _reject_symlink_path(output, home.resolve())
    expected = {"schema_version": 1, "ssh_identity": str(identity), "ssh_user": ssh_user}
    if output.exists():
        if read_json(output) != expected or output.stat().st_mode & 0o777 != 0o600:
            raise query_error("configuration_required", "identity_replacement_refused")
        return output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.parent.chmod(0o700)
    _atomic_text(output, json.dumps({"schema_version": 1, "ssh_identity": str(identity),
                                   "ssh_user": ssh_user}, ensure_ascii=False, sort_keys=True) + "\n")
    output.chmod(0o600)
    return output


def _stat_metadata(value):
    return [str(getattr(value, name)) for name in
            ("st_dev", "st_ino", "st_uid", "st_mode", "st_size", "st_mtime_ns", "st_ctime_ns")]


def _probe_binding(home):
    """Read private JSON only; bind its bytes and key filesystem metadata, never key contents."""
    file = code_query_user_config(home)
    try:
        _reject_symlink_path(file, home.resolve())
        if ".ssh" in file.resolve(strict=True).parts:
            raise ValueError()
        before = file.stat()
        if not file.is_file() or before.st_uid != os.getuid() or before.st_mode & 0o777 != 0o600 or before.st_size > 65536:
            raise ValueError()
        raw = file.read_bytes()
        config = json.loads(raw, object_pairs_hook=_unique_object)
        if not isinstance(config, dict) or set(config) != {"schema_version", "ssh_user", "ssh_identity"} or \
                type(config["schema_version"]) is not int or config["schema_version"] != 1:
            raise ValueError()
        identity = _validated_identity(config["ssh_user"], config["ssh_identity"])
        profile = company_profile.capture_profile(home)
        metadata = _stat_metadata(identity.stat())
        if _stat_metadata(file.stat()) != _stat_metadata(before):
            raise ValueError()
        hashed = lambda value: hashlib.sha256(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
        return {"company_profile_sha256": profile["company_profile_sha256"], "user_config_sha256": hashlib.sha256(raw).hexdigest(),
                "user_config_metadata_sha256": hashed(_stat_metadata(before)),
                "identity_sha256": hashed([config["ssh_user"], str(identity), *metadata])}
    except (OSError, ValueError, KitError, TypeError):
        raise query_error("configuration_required", "probe_configuration_changed") from None


def _assert_probe_binding(home, binding):
    if _probe_binding(home) != binding:
        raise query_error("configuration_required", "probe_configuration_changed")


def code_query_probe(package, home, skill_root):
    binding = _probe_binding(home)
    args = ["node", str(skill_root / "enterprise-prototype-code-query/scripts/code_query.js"),
            "--service-config", str(company_profile.root(home) / "service.json"),
            "--known-hosts", str(company_profile.root(home) / "known_hosts"),
            "--user-config", str(code_query_user_config(home)), "--company-profile-sha256", binding["company_profile_sha256"], "doctor"]
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=65)
        text = result.stdout if result.returncode == 0 else result.stderr.strip().splitlines()[-1]
        if len(text) > 65536:
            raise ValueError()
        payload = json.loads(text, object_pairs_hook=_unique_object)
    except (OSError, subprocess.TimeoutExpired, ValueError, IndexError, KitError):
        raise query_error("service_unavailable", "malformed_error") from None
    expected = {"status": "pass", "scope": "service", "checks": {"authentication": "pass", "list": "pass"}}
    if result.returncode == 0:
        if payload != expected:
            raise query_error("query_probe_blocked", "invalid_probe_output")
        _assert_probe_binding(home, binding)
        return expected
    if not isinstance(payload, dict) or set(payload) != {"status", "error", "message", "next_step"} or \
            payload["status"] not in QUERY_GUIDES or not all(isinstance(value, str) for value in payload.values()) or \
            payload["error"] not in QUERY_ERROR_CODES:
        raise query_error("service_unavailable", "malformed_error")
    # Never trust remote or subprocess messages: stable local explanations only.
    raise query_error(payload["status"], payload["error"])


def doctor_code_query(package, home):
    verify_kit(package)
    app, skill, _ = roots(home)
    if (app / "state.json").is_file() and read_json(app / "state.json").get("status") == "installed":
        with runtime(package) as api:
            doctor(package, home, api, query=False)
            return code_query_probe(package, home, skill)
    with tempfile.TemporaryDirectory(prefix="prototype-query-doctor-") as raw:
        destination = Path(raw)
        with zipfile.ZipFile(package / SKILL_ZIP) as archive:
            seen = set()
            for item in archive.infolist():
                relative = PurePosixPath(item.filename)
                if relative.is_absolute() or ".." in relative.parts or item.filename in seen or \
                        (item.external_attr >> 16) & 0o170000 == 0o120000:
                    raise KitError("归档路径不安全")
                seen.add(item.filename)
                if relative.parts[:1] == ("enterprise-prototype-code-query",):
                    archive.extract(item, destination)
        if not (destination / "enterprise-prototype-code-query/scripts/code_query.js").is_file():
            raise query_error("query_probe_blocked", "query_adapter_missing")
        return code_query_probe(package, home, destination)


def digest(file: Path) -> str:
    return hashlib.sha256(file.read_bytes()).hexdigest()


def host_fingerprint(file, expected_host):
    try:
        parts = file.read_text(encoding="ascii").split()
        if len(parts) != 3 or parts[0] != expected_host or parts[1] != "ssh-ed25519":
            raise ValueError()
        blob = base64.b64decode(parts[2], validate=True)
        return "SHA256:" + base64.b64encode(hashlib.sha256(blob).digest()).decode().rstrip("=")
    except (OSError, UnicodeError, ValueError):
        raise query_error("host_identity_blocked", "known_hosts_mismatch") from None


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise KitError(f"重复字段：{key}")
        result[key] = value
    return result


def read_json(file):
    return json.loads(file.read_text(encoding="utf-8"), object_pairs_hook=_unique_object)


def verify_kit(package: Path) -> dict:
    package = package.resolve()
    manifest_file = package / "kit-manifest.json"
    if manifest_file.is_symlink():
        raise KitError("包装清单不能是软链接")
    manifest = read_json(manifest_file)
    if set(manifest) != {"schema_version", "candidate_version", "kit_revision", "files"}:
        raise KitError("包装清单字段不合法")
    if (manifest["schema_version"], manifest["candidate_version"], manifest["kit_revision"]) != (1, VERSION, 1):
        raise KitError(f"不是已锁定的 {VERSION} 分发包")
    files = manifest["files"]
    if not isinstance(files, dict) or not set(FROZEN).issubset(files):
        raise KitError("包装清单缺少冻结发布实体")
    actual = set()
    for file in package.rglob("*"):
        if file.is_symlink():
            raise KitError("分发包不能包含软链接")
        if file.is_file() and file.name != ".DS_Store":
            actual.add(file.relative_to(package).as_posix())
    if actual != set(files) | {"kit-manifest.json"}:
        raise KitError("分发包文件有缺失或未发布内容，请重新下载")
    for relative, expected in files.items():
        parts = PurePosixPath(relative)
        if parts.is_absolute() or ".." in parts.parts or str(parts) != relative:
            raise KitError("包装清单存在越界路径")
        if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected):
            raise KitError("包装清单摘要不合法")
        if digest(package / relative) != expected:
            raise KitError(f"文件校验失败：{relative}；请重新下载，不要原地修补")
    for relative, expected in FROZEN.items():
        if files[relative] != expected:
            raise KitError(f"冻结发布实体被替换：{relative}")
    return manifest


@contextmanager
def runtime(package: Path):
    """Load only the verified archived runtime; no global pip install or bytecode."""
    verify_kit(package)
    previous_path = list(sys.path)
    previous_modules = {k: v for k, v in sys.modules.items()
                        if k == "enterprise_prd" or k.startswith("enterprise_prd.")}
    for key in previous_modules:
        del sys.modules[key]
    try:
        with tempfile.TemporaryDirectory(prefix="prototype-kit-runtime-") as raw:
            destination = Path(raw)
            with zipfile.ZipFile(package / SKILL_ZIP) as archive:
                for item in archive.infolist():
                    relative = PurePosixPath(item.filename)
                    if relative.is_absolute() or ".." in relative.parts or (item.external_attr >> 16) & 0o170000 == 0o120000:
                        raise KitError("归档路径不安全")
                archive.extractall(destination)
            sys.path[:0] = [str(destination / "runtime"), str(package / "vendor")]
            yield SimpleNamespace(
                install=importlib.import_module("enterprise_prd.install"),
                start=importlib.import_module("enterprise_prd.prototype.start"),
                updater=importlib.import_module("enterprise_prd.updater"),
                registry=importlib.import_module("enterprise_prd.registry"),
                manifest=importlib.import_module("enterprise_prd.manifest"),
                cli=importlib.import_module("enterprise_prd.cli"),
            )
    finally:
        sys.path[:] = previous_path
        for key in list(sys.modules):
            if key == "enterprise_prd" or key.startswith("enterprise_prd."):
                del sys.modules[key]
        sys.modules.update(previous_modules)


def app_root(home: Path) -> Path:
    return home.resolve() / ".local/share/enterprise-prototype" / VERSION


def roots(home):
    app = app_root(home)
    pair = app / f"release/versions/{VERSION}__{VERSION}"
    return app, pair / "skill", pair / "pack"


def environment_check() -> dict:
    if sys.platform != "darwin":
        raise KitError("此包仅验收 macOS；Windows/Linux 尚未验收，请联系维护者")
    if sys.version_info[:2] != (3, 12):
        raise KitError("需要 Python 3.12，请用 python3.12 运行本入口")
    versions = {}
    for program in ("node", "npm", "ssh"):
        executable = shutil.which(program)
        if executable is None:
            raise KitError(f"缺少 {program}；请先安装 Node.js 22 与 npm")
        result = subprocess.run([executable, "-V" if program == "ssh" else "--version"], text=True, capture_output=True, timeout=30)
        if result.returncode:
            raise KitError(f"无法运行 {program}")
        versions[program] = (result.stderr if program == "ssh" else result.stdout).strip()
    if not re.match(r"^v22\.", versions["node"]):
        raise KitError(f"需要 Node.js 22，当前为 {versions['node']}；请切换版本后重试")
    return {"status": "pass", "python": sys.version.split()[0], **versions}


@contextmanager
def local_environment(home):
    app = app_root(home)
    updates = {"PYTHONDONTWRITEBYTECODE": "1", "PYTHONNOUSERSITE": "1",
               "PLAYWRIGHT_BROWSERS_PATH": str(app / "browsers"),
               "npm_config_cache": str(app / "cache/npm")}
    before = {key: os.environ.get(key) for key in updates}
    os.environ.update(updates)
    try:
        yield
    finally:
        for key, value in before.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def release_check(package, home, api):
    verify_kit(package)
    _, skill, pack = roots(home)
    api.start.bind_archive_to_tree((package / SKILL_ZIP).read_bytes(), skill, "Skill",
                                   allowed_extra_prefixes=api.start.SKILL_GENERATED_PREFIXES)
    api.start.bind_archive_to_tree((package / PACK_ZIP).read_bytes(), pack, "Pack")


def browser_check(skill):
    shell = skill / "prototype-design-workflow/assets/developer-reader-shell"
    code = "const p=require('playwright');if(!require('fs').existsSync(p.chromium.executablePath()))process.exit(1)"
    result = subprocess.run(["node", "-e", code], cwd=shell, capture_output=True, text=True, timeout=30)
    if result.returncode:
        raise KitError("Playwright/Chromium 不可用，请联系维护者；不要跳过验收")


def state_for(home):
    app = app_root(home)
    state = read_json(app / "state.json")
    if set(state) != STATE_KEYS or state.get("status") != "installed" or state.get("version") != VERSION:
        raise KitError("此版本尚未安装或已回滚")
    receipt = _receipt_path(app, state["activation_receipt"], "activation")
    if digest(receipt) != state["activation_receipt_sha256"]:
        raise KitError("激活记录发生变化，请联系维护者")
    return state, receipt


def _receipt_path(app, name, kind):
    if not isinstance(name, str) or not re.fullmatch(kind + r"-[0-9a-f]{32}\.json", name):
        raise KitError("安装记录路径不合法")
    file = app / "receipts" / name
    if file.is_symlink() or file.resolve().parent != (app / "receipts").resolve():
        raise KitError("安装记录路径不合法")
    return file


def doctor(package, home, api, smoke=False, *, query=True, _bound=False):
    verify_kit(package)
    env = environment_check()
    _check_link_boundaries(home.resolve())
    app, skill, _ = roots(home)
    state, activation_file = state_for(home)
    activation = read_json(activation_file)
    if set(activation) != ACTIVATION_KEYS or (activation["schema_version"], activation["version"], activation["home"],
        activation["skills"], activation["links"]) != (2, VERSION, str(home.resolve()), list(SKILLS), 20):
        raise KitError("激活记录与当前环境不一致")
    receipt_file = _receipt_path(app, activation["link_receipt"], "install")
    query_file = _receipt_path(app, activation["code_query_receipt"], "code-query")
    if digest(receipt_file) != activation["link_receipt_sha256"] or digest(query_file) != activation["code_query_receipt_sha256"]:
        raise KitError("激活绑定的安装或查询记录发生变化")
    receipt, query_receipt = read_json(receipt_file), read_json(query_file)
    if set(receipt) != api.install.RECEIPT_KEYS or receipt["home"] != str(home.resolve()) or receipt["source"] != str(skill):
        raise KitError("安装记录与当前环境不一致")
    installed_smoke = activation["smoke"]
    if receipt["skill_names"] != list(SKILLS) or len(receipt["links"]) != 20 or \
            not isinstance(installed_smoke, dict) or set(installed_smoke) != {"status", "tests", "passed", "failed"} or \
            installed_smoke.get("status") != "pass" or type(installed_smoke.get("tests")) is not int or installed_smoke["tests"] < 32 or \
            installed_smoke.get("passed") != installed_smoke["tests"] or installed_smoke.get("failed") != 0 or \
            activation["dependency"].get("status") != "pass":
        raise KitError("激活记录缺少五个入口或完整 Smoke")
    release_check(package, home, api)
    if set(query_receipt) != QUERY_RECEIPT_KEYS or query_receipt["version"] != VERSION or query_receipt["schema_version"] != 3 or \
            query_receipt["checks"] != {"authentication": "pass", "list": "pass"} or \
            query_receipt["scope"] != "service" or \
            query_receipt["adapter_sha256"] != digest(package / SKILL_ZIP) or \
            query_receipt["service_config_sha256"] != digest(company_profile.root(home) / "service.json") or \
            query_receipt["known_hosts_sha256"] != digest(company_profile.root(home) / "known_hosts"):
        raise KitError("查询服务记录与冻结版本不一致")
    if query_receipt["host_fingerprint"] != host_fingerprint(company_profile.root(home) / "known_hosts", company_profile.capture_profile(home)["profile"]["service"]["host"]) or \
            query_receipt["adapter_version"] != VERSION or query_receipt["service_config_schema_version"] != 2:
        raise KitError("查询能力身份摘要不一致")
    if any(not isinstance(query_receipt[key], str) or re.fullmatch(r"[0-9a-f]{64}", query_receipt[key]) is None
           for key in ("identity_sha256", "user_config_sha256", "user_config_metadata_sha256")):
        raise KitError("查询能力身份元数据摘要不合法")
    if not _bound and not all(api.install.verify_shared_links(skill, home=home, skill_names=SKILLS).values()):
        raise KitError("共享入口发生变化；不要覆盖未知入口，请联系维护者")
    if digest(app / "bin/enterprise-prd") != state["launcher_sha256"]:
        raise KitError("本地启动器发生变化")
    if digest(app / "bin/enterprise-prototype-code-query") != state["code_query_launcher_sha256"]:
        raise KitError("代码查询启动器发生变化")
    with local_environment(home):
        browser_check(skill)
        smoke_result = f"install_receipt_{installed_smoke['passed']}/{installed_smoke['tests']}"
        if smoke:
            core = skill / "prototype-design-workflow"
            tests = tuple(sorted((core / "tests").glob("*.test.mjs")))
            completed = subprocess.run(["node", "--test", *(str(path) for path in tests)],
                                       check=False, capture_output=True, text=True, timeout=300)
            summary = completed.stdout + "\n" + completed.stderr
            match = re.search(r"(?m)^(?:#|ℹ) tests (\d+)\s*$", summary)
            passed = re.search(r"(?m)^(?:#|ℹ) pass (\d+)\s*$", summary)
            failed = re.search(r"(?m)^(?:#|ℹ) fail (\d+)\s*$", summary)
            if completed.returncode or not match or not passed or not failed or \
                    int(match.group(1)) < 32 or int(passed.group(1)) != int(match.group(1)) or int(failed.group(1)) != 0:
                raise KitError("完整 Prototype Chromium Smoke 未全部通过")
            smoke_result = f"rerun_pass_{passed.group(1)}/{match.group(1)}"
    captured = _probe_binding(home)
    if query_receipt["company_profile_sha256"] != captured["company_profile_sha256"] or activation["company_profile_sha256"] != captured["company_profile_sha256"]:
        raise KitError("公司配置绑定发生变化")
    if any(query_receipt[key] != captured[key] for key in ("identity_sha256", "user_config_sha256", "user_config_metadata_sha256")):
        raise KitError("个人身份绑定发生变化")
    if query:
        code_query_probe(package, home, skill)
    return {"status": "pass", "candidate_version": VERSION, "links": 20,
            "environment": env, "smoke": smoke_result}


def _atomic_text(file, value):
    file.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".kit-write-", dir=file.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, file)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _state_write(app, state):
    _atomic_text(app / "state.json", json.dumps(state, ensure_ascii=False, sort_keys=True) + "\n")


def _readonly_files(directory):
    for file in directory.rglob("*"):
        if file.is_file() and not file.is_symlink() and "node_modules" not in file.parts:
            file.chmod(file.stat().st_mode & ~0o222)


def _check_install_location(home):
    home = home.resolve()
    if not home.is_dir():
        raise KitError("用户目录必须已存在")
    app = app_root(home)
    for directory in [app, *app.parents]:
        if directory == home:
            break
        if directory.is_symlink():
            raise KitError("安装目录不能经过软链接")
    return app


def _check_link_boundaries(home):
    for client in (".agents", ".codex", ".claude", ".openclaw"):
        for directory in (home / client, home / client / "skills"):
            if directory.is_symlink():
                raise KitError("共享入口父目录不能是软链接")


@contextmanager
def _install_lock(home):
    import fcntl
    app = _check_install_location(home)
    app.mkdir(parents=True, exist_ok=True)
    with (app / ".kit.lock").open("a+b") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise KitError("另一个安装/回滚正在进行，请等待它结束") from exc
        try:
            yield app
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def start_text(home):
    app, skill, pack = roots(home)
    return f"""# 新对话启动提示（本机专用，不放进公共包）

使用 enterprise-prototype-chain，按 Candidate {VERSION} 为当前已评审需求制作原型。
所有原生命令必须通过插件 `run --workspace <本需求目录> -- prototype ...` 调用需求绑定执行器。
代码查询只使用 `{app / 'bin/enterprise-prototype-code-query'}`；新 Run 前先执行该 launcher `doctor`。
先读取本文件并核对环境，再按 Skill 流程执行；不要修改安装目录。

- Canary Manifest：`{app / 'kit/registry/prototype-canary-manifest.yaml'}`
- Registry：`{app / 'kit/registry'}`
- Skill Root：`{skill}`
- Pack Root：`{pack}`
- Source Registry：`{pack / 'surfaces/surface-guidance.yaml'}`
- Source Registry Version：`{VERSION}`
- 完整参数说明：读取上方 Skill Root 中的核心 Skill 与 references。

Entry 前只读用户指定的当前 PRD 和适用审查，不自动跟随引用。生成真实就绪记录，\n按 --readiness-assessment 创建 Entry v2；旧 v1 Run 不迁移，须新建 Run。\n原型就绪与 Spec 就绪分离；NOT_ASSESSED 如实留档，不自动转 PASS，自审不升级独立审查。\n请由用户提供本次 PRD、适用审查证明及项目材料位置。保持既有系统增量、HTML、
light/standard、六类本地来源的边界；不能将缺少材料或授权自行填写为 PASS。
Run 和业务输出保存在用户当前项目的独立目录，不放在本安装目录。
明确授权的本机源码使用 local-code-capture；与远程采集合并用 code-source-collect，失败不静默回退。
首次生成须限定查找并由用户确认没有本需求旧源，使用 first-baseline-create，不做伪旧 HTML。
这两个分支不免除真实页面、代码、同版本截图和独立复核；具体契约读取核心 Skill 的对应 reference。
只采集门禁所需信息，不要求同事重新制作分发工作包。
Builder 后使用全新对话执行 Review；同一 Run 保持同一本机文件环境，不能跨机器续跑。
任何 restart_required 都停止该 Run，反馈维护者；不要绕过校验或修改 Candidate。
安装成功不代表真人案例通过，也不授权 PRD 回写、主链接入或 Stable 发布。
"""


def run_command(package, home, api, arguments, *, _bound=False):
    doctor(package, home, api, query=False, _bound=_bound)
    if arguments[:2] in (["prototype", "entry-create"], ["prototype", "source-set-create"]):
        try:
            code_query_probe(package, home, roots(home)[1])
        except GateError:
            creating_entry = arguments[1] == "entry-create"
            payload = {"status": "blocked" if creating_entry else "restart_required",
                       "error": "code_query_unavailable", "next_step": "完成个人 SSH 配置和查询探测后创建新 Run"}
            if creating_entry:
                payload["baseline_evidence"] = "block"
            print(json.dumps(payload, ensure_ascii=False), file=sys.stderr)
            return 6
    with local_environment(home):
        sys.argv = ["enterprise-prd", *arguments]
        return api.cli.main()


def _recover_interrupted_links(api, links):
    """The frozen installer handles Exception; cover Ctrl-C without touching unknown targets."""
    changed = False
    for item in links:
        target = Path(item["path"])
        if target.is_symlink():
            current = os.readlink(target)
            allowed = {item["installed_target"]}
            if item["previous_state"] == "symlink":
                allowed.add(item["previous_target"])
            if current not in allowed:
                raise KitError("中断恢复发现未知入口，拒绝覆盖；请联系维护者")
            changed |= item["previous_state"] != "symlink" or current != item["previous_target"]
        elif target.exists():
            raise KitError("中断恢复发现真实目录，拒绝覆盖；请联系维护者")
        else:
            changed |= item["previous_state"] != "absent"
    if changed:
        api.install._restore_links(links)


def install(package, home, api):
    verify_kit(package)
    environment_check()
    with _install_lock(home) as app:
        _check_link_boundaries(home.resolve())
        if (app / "state.json").is_file() and read_json(app / "state.json").get("status") == "installed":
            doctor(app / "kit", home, api)
            return {"status": "already_installed", "start_here": str(app / "START-HERE.md")}
        stored = app / "kit"
        if stored.exists():
            if verify_kit(stored) != verify_kit(package):
                raise KitError("同版本不能替换成不同分发包")
        else:
            with tempfile.TemporaryDirectory(prefix=".kit-copy-", dir=app) as raw:
                staged = Path(raw) / "kit"
                shutil.copytree(package, staged, ignore=shutil.ignore_patterns(".DS_Store"))
                verify_kit(staged)
                os.replace(staged, stored)
        manifest = api.manifest.manifest_from_file(stored / "registry/prototype-canary-manifest.yaml")
        api.updater.apply_manifest(manifest, api.registry.FileRegistry(stored / "registry"), app / "release")
        release_check(stored, home, api)
        _, skill, pack = roots(home)
        previous_links = api.install._capture_links(home.resolve(), skill, SKILLS)
        receipt_file = app / "receipts" / f"install-{uuid.uuid4().hex}.json"
        receipt = None
        query_file = activation_file = None
        launchers = [app / "bin/enterprise-prd", app / "bin/enterprise-prototype-code-query"]
        try:
            print("正在恢复依赖和 Chromium，并执行完整 Prototype Smoke；完成前不会发布共享入口。", flush=True)
            with local_environment(home):
                dependency, smoke = api.install._restore_dependencies(skill)
                release_check(stored, home, api)
                browser_check(skill)
                if not code_query_user_config(home).exists() and sys.stdin.isatty():
                    configure_code_query(home, input("个人 SSH 用户名：").strip(),
                                         input("已有个人密钥路径（不输入正文或口令）：").strip())
                binding = _probe_binding(home)
                probe = code_query_probe(stored, home, skill)
            _assert_probe_binding(home, binding)
            query_file = app / "receipts" / f"code-query-{uuid.uuid4().hex}.json"
            query_receipt = {"schema_version": 3, "version": VERSION,
                "adapter_sha256": digest(stored / SKILL_ZIP),
                "service_config_sha256": digest(company_profile.root(home) / "service.json"),
                "known_hosts_sha256": digest(company_profile.root(home) / "known_hosts"),
                "host_fingerprint": host_fingerprint(company_profile.root(home) / "known_hosts", company_profile.capture_profile(home)["profile"]["service"]["host"]),
                "adapter_version": VERSION, "service_config_schema_version": 2,
                **binding,
                "scope": probe["scope"], "checks": probe["checks"],
                "checked_at": datetime.now(timezone.utc).isoformat()}
            api.install.write_once_json(query_file, query_receipt)
            release_check(stored, home, api)
            _check_link_boundaries(home.resolve())
            _assert_probe_binding(home, binding)
            receipt = api.install.install_shared_links(skill, home=home, skill_names=SKILLS,
                receipt_path=receipt_file, restore_prototype_dependencies=False)
            release_check(stored, home, api)
            _assert_probe_binding(home, binding)
            if not all(api.install.verify_shared_links(skill, home=home, skill_names=SKILLS).values()):
                raise KitError("共享入口激活复验失败")
            launcher = launchers[0]
            args = [sys.executable, "-I", "-S", "-B", str(stored / "prototype-kit.py"), "run", "--home", str(home.resolve()), "--"]
            executable_dirs = dict.fromkeys(str(Path(shutil.which(name)).parent) for name in ("node", "npm"))
            local_path = os.pathsep.join(executable_dirs)
            _atomic_text(launcher, "#!/bin/sh\nexport PATH=" + shlex.quote(local_path) + ':"$PATH"\nexec '
                         + shlex.join(args) + ' "$@"\n')
            launcher.chmod(0o555)
            query_launcher = launchers[1]
            query_args = [shutil.which("node"), str(skill / "enterprise-prototype-code-query/scripts/code_query.js"),
                          "--service-config", str(company_profile.root(home) / "service.json"),
                          "--known-hosts", str(company_profile.root(home) / "known_hosts"),
                          "--user-config", str(code_query_user_config(home)), "--company-profile-sha256", binding["company_profile_sha256"]]
            _atomic_text(query_launcher, "#!/bin/sh\nexec " + shlex.join(query_args) + ' "$@"\n')
            query_launcher.chmod(0o555)
            _readonly_files(stored)
            _readonly_files(skill.resolve())
            _readonly_files(pack.resolve())
            activation_file = app / "receipts" / f"activation-{uuid.uuid4().hex}.json"
            activation = {"schema_version": 2, "company_profile_sha256": binding["company_profile_sha256"], "version": VERSION, "home": str(home.resolve()),
                "skills": list(SKILLS), "links": 20, "dependency": dependency, "smoke": smoke,
                "link_receipt": receipt_file.name, "link_receipt_sha256": digest(receipt_file),
                "code_query_receipt": query_file.name, "code_query_receipt_sha256": digest(query_file),
                "activated_at": datetime.now(timezone.utc).isoformat()}
            _assert_probe_binding(home, binding)
            api.install.write_once_json(activation_file, activation)
            _assert_probe_binding(home, binding)
            _atomic_text(app / "START-HERE.md", start_text(home))
            _assert_probe_binding(home, binding)
            _state_write(app, {"status": "installed", "version": VERSION,
                "activation_receipt": activation_file.name, "activation_receipt_sha256": digest(activation_file),
                "launcher_sha256": digest(launcher), "code_query_launcher_sha256": digest(query_launcher)})
            _assert_probe_binding(home, binding)
        except BaseException:
            if receipt is not None:
                api.install.rollback_shared_links(receipt_file, home=home)
            else:
                _recover_interrupted_links(api, previous_links)
            for file in [app / "START-HERE.md", *launchers]:
                if file.is_file() and not file.is_symlink():
                    file.unlink()
            state_file = app / "state.json"
            if activation_file is not None and state_file.is_file() and \
                    read_json(state_file).get("activation_receipt") == activation_file.name:
                state_file.unlink()
            for file in [query_file, receipt_file, activation_file]:
                if file is not None and file.is_file() and not file.is_symlink():
                    file.unlink()
            raise
        return {"status": "installed", "candidate_version": VERSION, "links": 20,
                "smoke": f"{smoke['passed']}/{smoke['tests']}", "start_here": str(app / "START-HERE.md")}


def rollback(package, home, api):
    verify_kit(package)
    with _install_lock(home) as app:
        _check_link_boundaries(home.resolve())
        state, activation_file = state_for(home)
        activation = read_json(activation_file)
        if set(activation) != ACTIVATION_KEYS or activation["home"] != str(home.resolve()) or activation["version"] != VERSION:
            raise KitError("激活记录不合法")
        receipt_file = _receipt_path(app, activation["link_receipt"], "install")
        if digest(receipt_file) != activation["link_receipt_sha256"]:
            raise KitError("链接记录发生变化")
        result = api.install.rollback_shared_links(receipt_file, home=home)
        state["status"] = "rolled_back"
        _state_write(app, state)
        return {**result, "retained": "版本文件、安装记录和业务输出均保留"}


def dispatch_bound(binding, arguments, verify_bound, invoke):
    verified = verify_bound(binding)
    if not arguments or arguments[0] != "prototype":
        raise ValueError("PROTOTYPE_ONLY")
    return invoke(verified["runtime_root"], arguments)


def main():
    parser = argparse.ArgumentParser(description=f"Enterprise Prototype {VERSION} 一键使用包")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("verify", help="只读校验下载包，不要求 Node/npm")
    for name in ("install", "doctor", "rollback", "prompt", "feedback", "run", "configure-code-query", "doctor-code-query"):
        sub = commands.add_parser(name)
        sub.add_argument("--home", type=Path, default=Path.home(), help="默认当前用户目录；仅隔离测试时覆盖")
        if name == "doctor":
            sub.add_argument("--smoke", action="store_true", help="额外重跑全部 Chromium 核心测试")
        if name == "run":
            sub.add_argument("arguments", nargs=argparse.REMAINDER)
        if name == "configure-code-query":
            sub.add_argument("--ssh-user")
            sub.add_argument("--ssh-identity")
    args = parser.parse_args()
    if args.command == "run":
        if args.arguments[:1] == ["--"]:
            args.arguments = args.arguments[1:]
        if args.arguments[:1] != ["prototype"]:
            parser.error("本入口只接受 prototype 子命令，不替换既有 PRD CLI")
    package = Path(__file__).resolve().parent
    preflight = True
    try:
        verify_kit(package)
        if args.command == "verify":
            result = {"status": "pass", "candidate_version": VERSION}
        elif args.command == "feedback":
            result = {"candidate_version": VERSION, "prototype_run_id": "",
                      "delivery": "", "coverage": "", "gate_result": "",
                      "stable_error_code": "", "needs_author_explanation": "",
                      "actionable_issue": "", "note": "人工填入；不要粘贴 PRD、截图、代码或客户数据"}
        elif args.command == "configure-code-query":
            user, identity = args.ssh_user, args.ssh_identity
            if not user or not identity:
                if not sys.stdin.isatty():
                    raise query_error("configuration_required", "configuration_missing")
                user = user or input("个人 SSH 用户名：").strip()
                identity = identity or input("已有个人密钥路径（不输入正文、密码或口令）：").strip()
            configure_code_query(args.home.expanduser().resolve(), user, identity)
            result = {"status": "configured", "next_step": "重新运行 install 以完成真实 SSH 探测"}
        elif args.command == "doctor-code-query":
            result = doctor_code_query(package, args.home.expanduser().resolve())
        else:
            home = args.home.expanduser().resolve()
            with runtime(package) as api:
                if args.command == "install":
                    result = install(package, home, api)
                elif args.command == "rollback":
                    result = rollback(package, home, api)
                elif args.command == "doctor":
                    result = doctor(package, home, api, args.smoke)
                else:
                    if args.command == "prompt":
                        doctor(package, home, api)
                        print(start_text(home))
                        return 0
                    return run_command(package, home, api, args.arguments)
        print(json.dumps(result, ensure_ascii=False))
        if args.command == "install":
            print(f"安装完成。重启 Agent 或新建对话，然后发送：请读取 {result['start_here']} 并开始。")
        return 0
    except GateError as exc:
        print(json.dumps({"status": exc.status, "error": exc.error, "message": str(exc),
                          "next_step": exc.next_step}, ensure_ascii=False), file=sys.stderr)
        return 4 if exc.status == "configuration_required" else 5
    except (Exception, KeyboardInterrupt) as exc:
        restart = args.command == "run" and preflight
        print(json.dumps({"status": "restart_required" if restart else "blocked", "error": type(exc).__name__, "message": str(exc),
                          "next_step": "保留输出并联系维护者；不要绕过门禁或手改 Candidate"}, ensure_ascii=False), file=sys.stderr)
        return 6 if restart else 1


if __name__ == "__main__":
    raise SystemExit(main())
