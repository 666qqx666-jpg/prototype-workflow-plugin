'use strict';
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const crypto = require('node:crypto');
const {spawnSync} = require('node:child_process');
const SERVICE_KEYS = new Set(['schema_version', 'host', 'port', 'remote_script', 'known_hosts_sha256']);
const USER_KEYS = new Set(['schema_version', 'ssh_user', 'ssh_identity']);
const COMMANDS = new Set(['list', 'overview', 'search', 'read']);
const ID = /^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/;
const USER = /^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/;
const MAX_OUTPUT = 4 * 1024 * 1024;
const GUIDES = {
  configuration_required: ['个人 SSH 配置缺失或不合法', '运行 prototype-kit.py configure-code-query；选择当前用户 .ssh 内权限为 600 的个人密钥'],
  authorization_required: ['中心机拒绝当前个人 SSH 身份', '先核对已有用户名、密钥和 ssh-agent；确实未授权时联系权限负责人'],
  host_identity_blocked: ['中心机主机身份不符合冻结配置', '联系候选包维护者通过可信渠道核实主机公钥'],
  service_unavailable: ['代码查询服务不可用', '联系代码查询服务维护者后重试'],
  query_probe_blocked: ['查询结果未通过校验', '检查服务结果或当前任务项目权限'],
  invalid_query: ['代码查询参数不合法', '使用 doctor/list/overview/search/read 的限定参数'],
};
class QueryError extends Error {
  constructor(code, error = code) {super(GUIDES[code][0]); this.code = code; this.error = error;}
  toJSON() {return {status: this.code, error: this.error, message: this.message, next_step: GUIDES[this.code][1]};}
}
function closed(value, keys) {
  return value && !Array.isArray(value) && typeof value === 'object' &&
    Object.keys(value).length === keys.size && Object.keys(value).every(key => keys.has(key));
}
// JSON.parse accepts repeated keys. Parse the token structure first, including escaped keys.
function parseUniqueJson(text) {
  JSON.parse(text);
  const tokens = text.match(/"(?:\\[\s\S]|[^"\\])*"|-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?|true|false|null|[{}\[\],:]/g);
  let index = 0;
  function value() {
    const token = tokens[index++];
    if (token === '{') {
      const keys = new Set();
      if (tokens[index] !== '}') do {
        const key = JSON.parse(tokens[index++]);
        if (keys.has(key)) throw new Error(); keys.add(key); index++; value();
      } while (tokens[index] === ',' && ++index);
      index++;
    } else if (token === '[') {
      if (tokens[index] !== ']') do {value();} while (tokens[index] === ',' && ++index);
      index++;
    }
  }
  value();
  return JSON.parse(text);
}
function metadata(file) {
  const value = fs.statSync(file, {bigint: true});
  return ['dev', 'ino', 'uid', 'mode', 'size', 'mtimeNs', 'ctimeNs'].map(key => String(value[key]));
}
function readJsonSnapshot(file, code = 'configuration_required') {
  try {
    const resolved = fs.realpathSync(file), stat = fs.statSync(file);
    if (fs.lstatSync(file).isSymbolicLink() || resolved.split(path.sep).includes('.ssh') ||
        !stat.isFile() || stat.size > 65536 || (code === 'configuration_required' && ((stat.mode & 0o077) || stat.uid !== process.getuid()))) throw new Error();
    const before = metadata(file);
    const text = fs.readFileSync(file, 'utf8');
    const parsed = parseUniqueJson(text);
    if (JSON.stringify(before) !== JSON.stringify(metadata(file))) throw new Error();
    return {value: parsed, stamp: [crypto.createHash('sha256').update(text).digest('hex'), before]};
  } catch {throw new QueryError(code, 'invalid_configuration');}
}
function readClosedJson(file, code = 'configuration_required') {return readJsonSnapshot(file, code).value;}
function validateRemoteScript(script) {
  if (typeof script !== 'string' ||
      !/^\/(?:[A-Za-z0-9_-]+\/)*[A-Za-z0-9_.-]+\.js$/.test(script) ||
      script.split('/').slice(1).some(p => p === '.' || p === '..')) {
    throw new QueryError('host_identity_blocked', 'invalid_service_config');
  }
  return script;
}
function validateServiceConfig(config, knownHosts) {
  if (!closed(config, SERVICE_KEYS) || config.schema_version !== 2 || require('node:net').isIP(config.host) !== 4 ||
      config.port !== 22 || !validateRemoteScript(config.remote_script) ||
      typeof config.known_hosts_sha256 !== 'string' || !/^[0-9a-f]{64}$/.test(config.known_hosts_sha256))
    throw new QueryError('host_identity_blocked', 'invalid_service_config');
  try {
    if (!path.isAbsolute(knownHosts) || fs.lstatSync(knownHosts).isSymbolicLink() || !fs.statSync(knownHosts).isFile()) throw new Error();
    if (crypto.createHash('sha256').update(fs.readFileSync(knownHosts)).digest('hex') !== config.known_hosts_sha256) throw new Error();
  } catch {throw new QueryError('host_identity_blocked', 'known_hosts_mismatch');}
  return config;
}
function validateUserConfig(config, home = os.userInfo().homedir) {
  if (!closed(config, USER_KEYS) || config.schema_version !== 1 || typeof config.ssh_user !== 'string' ||
      !USER.test(config.ssh_user) || typeof config.ssh_identity !== 'string' ||
      /[\x00-\x1f\x7f]/.test(config.ssh_identity)) throw new QueryError('configuration_required', 'invalid_user_config');
  try {
    const ownerHome = fs.realpathSync(home);
    const raw = config.ssh_identity.startsWith('~/') ? path.join(ownerHome, config.ssh_identity.slice(2)) : config.ssh_identity;
    if (!path.isAbsolute(raw) || fs.lstatSync(raw).isSymbolicLink()) throw new Error();
    const identity = fs.realpathSync(raw), root = path.join(ownerHome, '.ssh');
    const relative = path.relative(root, identity), stat = fs.statSync(identity);
    if (!relative || relative.startsWith('..') || path.isAbsolute(relative) || !stat.isFile() ||
        (stat.mode & 0o077) || stat.uid !== process.getuid()) throw new Error();
    return {user: config.ssh_user, identity}; // never read private key bytes
  } catch {throw new QueryError('configuration_required', 'identity_invalid');}
}
function validateQuery(command, args) {
  const invalid = () => {throw new QueryError('invalid_query');};
  if (!COMMANDS.has(command) || !Array.isArray(args) || args.some(arg => typeof arg !== 'string' || /[\x00-\x1f\x7f]/.test(arg))) invalid();
  if (command === 'list') {if (args.length) invalid();}
  if (command === 'overview') {if (args.length !== 1 || !ID.test(args[0])) invalid();}
  if (command === 'read') {
    if (args.length !== 2 || !ID.test(args[0]) || !args[1] || args[1].startsWith('/') || args[1].includes('\\') ||
        args[1].split('/').some(part => !part || part === '..' || part === '.')) invalid();
  }
  if (command === 'search') {
    if (args.length !== 5 || !args[0] || args[0].length > 1024 || args[1] !== '--project' || !ID.test(args[2]) ||
        args[3] !== '--limit' || !/^(?:[1-9]|[1-4][0-9]|50)$/.test(args[4])) invalid();
  }
  return args;
}
function shellQuote(value) {return "'" + value.replace(/'/g, "'\\''") + "'";}
function buildRemoteCommand(script, command, args) {
  validateRemoteScript(script);
  validateQuery(command, args);
  return 'node ' + [script, command, ...args].map(shellQuote).join(' ');
}
function buildSshArgs({host, port, knownHosts, user, identity, remoteCommand}) {
  if (typeof knownHosts !== 'string' || /[%"\\\x00-\x1f\x7f]/.test(knownHosts))
    throw new QueryError('host_identity_blocked', 'unsafe_known_hosts_path');
  const knownHostsOption = /\s/.test(knownHosts) ? `"${knownHosts}"` : knownHosts;
  return ['-F', '/dev/null', '-p', String(port), '-i', identity,
    ...['BatchMode=yes', 'IdentitiesOnly=yes', 'PubkeyAuthentication=yes', 'PasswordAuthentication=no',
      'KbdInteractiveAuthentication=no', 'StrictHostKeyChecking=yes', `UserKnownHostsFile=${knownHostsOption}`,
      'GlobalKnownHostsFile=/dev/null', 'ForwardAgent=no', 'ClearAllForwardings=yes', 'RequestTTY=no',
      'PermitLocalCommand=no', 'ProxyCommand=none', 'ProxyJump=none', 'ControlMaster=no',
      'ControlPath=none', 'ConnectTimeout=5'].flatMap(option => ['-o', option]), `${user}@${host}`, remoteCommand];
}
function classifySshFailure(status, stderr = '') {
  if (/HOST IDENTIFICATION HAS CHANGED|Host key verification failed|No .* host key is known/i.test(stderr))
    return new QueryError('host_identity_blocked', 'host_key_verification_failed');
  if (status === 255 && /Permission denied|authentication failed|sign_and_send_pubkey/i.test(stderr))
    return new QueryError('authorization_required', 'authentication_failed');
  return new QueryError('service_unavailable', 'query_service_failed');
}
function captureConfiguration(options, dependencies) {
  const serviceJson = readJsonSnapshot(options.serviceConfig, 'host_identity_blocked');
  const service = validateServiceConfig(serviceJson.value, options.knownHosts);
  const profileJson = readJsonSnapshot(path.join(path.dirname(options.serviceConfig), 'profile.json'), 'host_identity_blocked');
  const profile = profileJson.value;
  if (!closed(profile, new Set(['schema_version','service','known_hosts'])) || profile.schema_version !== 1 ||
      typeof options.companyProfileSha256 !== 'string' || profileJson.stamp[0] !== options.companyProfileSha256 ||
      !closed(profile.service, SERVICE_KEYS) || [...SERVICE_KEYS].some(key => profile.service[key] !== service[key]) ||
      typeof profile.known_hosts !== 'string' || crypto.createHash('sha256').update(profile.known_hosts).digest('hex') !== service.known_hosts_sha256)
    throw new QueryError('host_identity_blocked', 'invalid_service_config');
  const privateJson = readJsonSnapshot(options.userConfig);
  const user = validateUserConfig(privateJson.value, dependencies.home);
  let knownHostsStamp;
  try {
    const before = metadata(options.knownHosts);
    const bytes = fs.readFileSync(options.knownHosts);
    if (JSON.stringify(before) !== JSON.stringify(metadata(options.knownHosts)) ||
        crypto.createHash('sha256').update(bytes).digest('hex') !== service.known_hosts_sha256) throw new Error();
    knownHostsStamp = [service.known_hosts_sha256, before];
  } catch {throw new QueryError('host_identity_blocked', 'known_hosts_mismatch');}
  let identityMetadata;
  try {identityMetadata = metadata(user.identity);} catch {throw new QueryError('configuration_required', 'identity_invalid');}
  return {service, user, stamp: JSON.stringify([profileJson.stamp, serviceJson.stamp, privateJson.stamp, knownHostsStamp,
    user.user, user.identity, identityMetadata])};
}
function assertConfiguration(options, dependencies, snapshot) {
  if (captureConfiguration(options, dependencies).stamp !== snapshot.stamp)
    throw new QueryError('configuration_required', 'probe_configuration_changed');
}
function executePinnedQuery(options, command, args, dependencies, snapshot) {
  validateQuery(command, args);
  assertConfiguration(options, dependencies, snapshot);
  const {service, user} = snapshot; // argv uses the captured identity, never a later reload
  const argv = buildSshArgs({...service, ...user, knownHosts: options.knownHosts,
    remoteCommand: buildRemoteCommand(service.remote_script, command, args)});
  let result;
  try {result = (dependencies.spawnSync || spawnSync)('ssh', argv, {shell: false, encoding: 'utf8', timeout: 30000, maxBuffer: MAX_OUTPUT});}
  catch {throw new QueryError('service_unavailable', 'spawn_failed');}
  if (result.error?.code === 'ENOENT') throw new QueryError('configuration_required', 'ssh_missing');
  if (result.error || typeof result.stdout !== 'string' || Buffer.byteLength(result.stdout) > MAX_OUTPUT)
    throw new QueryError('service_unavailable', 'query_output_unavailable');
  if (result.status !== 0) throw classifySshFailure(result.status, result.stderr);
  assertConfiguration(options, dependencies, snapshot);
  return result.stdout;
}
function executeQuery(options, command, args, dependencies = {}) {
  validateQuery(command, args);
  const output = executePinnedQuery(options, command, args, dependencies, captureConfiguration(options, dependencies));
  if (command === 'list') validateServiceList(output);
  else {
    if (!output.trim()) throw new QueryError('query_probe_blocked', 'empty_query_output');
    rejectRemoteError(output);
  }
  return output;
}
function validateServiceList(text) {
  let payload;
  try {payload = parseUniqueJson(text);} catch {throw new QueryError('query_probe_blocked', 'invalid_list_output');}
  if (!payload || Array.isArray(payload) || typeof payload !== 'object' ||
      payload.success !== true || !payload.projects || Array.isArray(payload.projects) ||
      typeof payload.projects !== 'object' || Object.hasOwn(payload, 'error') || Object.hasOwn(payload, 'errors'))
    throw new QueryError('query_probe_blocked', 'invalid_list_output');
}
function rejectRemoteError(text) {
  try {JSON.parse(text);} catch {return;} // read may return plain code
  let payload;
  try {payload = parseUniqueJson(text);} catch {throw new QueryError('query_probe_blocked', 'invalid_query_output');}
  if (payload && typeof payload === 'object' && !Array.isArray(payload) &&
      (Object.hasOwn(payload, 'error') || Object.hasOwn(payload, 'errors') ||
       (Object.hasOwn(payload, 'success') && payload.success !== true)))
    throw new QueryError('query_probe_blocked', 'query_result_rejected');
}
function runProbe(options, dependencies = {}) {
  const snapshot = captureConfiguration(options, dependencies);
  validateServiceList(executePinnedQuery(options, 'list', [], dependencies, snapshot));
  return {status:'pass', scope:'service', checks:{authentication:'pass', list:'pass'}};
}
module.exports = {QueryError, readClosedJson, validateServiceConfig, validateUserConfig, validateQuery,
  shellQuote, buildRemoteCommand, buildSshArgs, classifySshFailure, executeQuery, runProbe};
