'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const crypto = require('node:crypto');
const ssh = require('../lib/ssh');

function fixture(t) {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), 'prototype-query-'));
  t.after(() => fs.rmSync(home, {recursive: true, force: true}));
  fs.mkdirSync(path.join(home, '.ssh'));
  const identity = path.join(home, '.ssh', 'fixture-key');
  fs.writeFileSync(identity, 'synthetic-not-a-private-key', {mode: 0o600});
  const knownHosts = path.join(home, 'known_hosts');
  fs.writeFileSync(knownHosts, 'synthetic-public-host-fixture\n');
  const serviceConfig = path.join(home, 'service.json');
  fs.writeFileSync(serviceConfig, JSON.stringify({schema_version: 2, host: '192.0.2.10',
    port: 22, remote_script: '/srv/query.js',
    known_hosts_sha256: crypto.createHash('sha256').update(fs.readFileSync(knownHosts)).digest('hex')}));
  const userConfig = path.join(home, 'user.json');
  fs.writeFileSync(userConfig, JSON.stringify({schema_version: 1, ssh_user: 'pilot.user',
    ssh_identity: identity}), {mode: 0o600});
  const profilePath = path.join(home, 'profile.json');
  fs.writeFileSync(profilePath, JSON.stringify({schema_version:1,service:JSON.parse(fs.readFileSync(serviceConfig,'utf8')),known_hosts:fs.readFileSync(knownHosts,'utf8')}), {mode:0o400});
  const companyProfileSha256 = crypto.createHash('sha256').update(fs.readFileSync(profilePath)).digest('hex');
  return {home, identity, knownHosts, serviceConfig, userConfig, companyProfileSha256};
}
const fails = code => error => error instanceof ssh.QueryError && error.code === code &&
  !JSON.stringify(error.toJSON()).includes('fixture-key') && !JSON.stringify(error.toJSON()).includes('pilot.user');

test('service Doctor needs one list, no fixed project or overview', t => {
  const f = fixture(t), commands = [];
  const result = ssh.runProbe(f, {home:f.home, spawnSync:(_, args) => {
    commands.push(args.at(-1)); return {status:0, stdout:'{"success":true,"projects":{}}'};
  }});
  assert.deepEqual(result, {status:'pass', scope:'service', checks:{authentication:'pass', list:'pass'}});
  assert.equal(commands.length, 1);
  assert.ok(commands[0].endsWith("'list'"));
});
test('service Doctor rejects error-only, false, wrong-type and duplicate JSON', t => {
  const f = fixture(t);
  for (const stdout of ['{"error":"access denied"}', '{"success":false,"projects":{}}',
    '{"success":"true","projects":{}}', '{"success":true,"projects":null}',
    '{"success":true,"projects":[]}', '{"success":true,"projects":{},"error":null}',
    '{"success":true,"projects":{},"errors":[]}',
    '{"success":false,"su\\u0063cess":true,"projects":{}}',
    '{"success":true,"projects":{},"projects":{}}', 'not JSON', '[]', '{}']) {
    assert.throws(() => ssh.runProbe(f, {home:f.home, spawnSync:()=>({status:0, stdout})}), fails('query_probe_blocked'));
  }
});
test('target query rejects explicit errors, duplicate keys and wrong success types', t => {
  const f = fixture(t);
  for (const stdout of ['{"error":"access denied"}', '{"errors":[]}',
    '{"success":false}', '{"success":"true"}', '{"success":1}',
    '{"success":false,"success":true}', '{"project":"a","pro\\u006aect":"b"}', '   ']) {
    for (const [command,args] of [['overview',['selected-project']], ['read',['selected-project','page.js']],
      ['search',['page','--project','selected-project','--limit','2']]])
      assert.throws(() => ssh.executeQuery(f, command, args, {home:f.home, spawnSync:()=>({status:0, stdout})}), fails('query_probe_blocked'));
  }
});
test('Probe binds one configuration and detects identity changes without reading key contents', t => {
  const f = fixture(t); let calls = 0;
  assert.throws(() => ssh.runProbe(f, {home: f.home, spawnSync: () => {
    if (calls++ === 0) {
      fs.writeFileSync(f.userConfig, JSON.stringify({schema_version: 1, ssh_user: 'other.pilot', ssh_identity: f.identity}));
      return {status: 0, stdout: '{"success":true,"projects":{}}'};
    }
    assert.fail('Doctor must not call a second command');
  }}), fails('configuration_required'));
  assert.equal(calls, 1);
});

test('SSH args enforce pinned host and disable forwarding', () => {
  assert.deepEqual(ssh.buildSshArgs({host: '203.0.113.10', port: 22,
    knownHosts: '/tmp/pinned-known-hosts', user: 'pilot.user', identity: '/tmp/id_ed25519',
    remoteCommand: "node '/srv/query.js' 'list'"}), [
    '-F', '/dev/null', '-p', '22', '-i', '/tmp/id_ed25519',
    '-o', 'BatchMode=yes', '-o', 'IdentitiesOnly=yes', '-o', 'PubkeyAuthentication=yes',
    '-o', 'PasswordAuthentication=no', '-o', 'KbdInteractiveAuthentication=no',
    '-o', 'StrictHostKeyChecking=yes', '-o', 'UserKnownHostsFile=/tmp/pinned-known-hosts',
    '-o', 'GlobalKnownHostsFile=/dev/null', '-o', 'ForwardAgent=no',
    '-o', 'ClearAllForwardings=yes', '-o', 'RequestTTY=no', '-o', 'PermitLocalCommand=no',
    '-o', 'ProxyCommand=none', '-o', 'ProxyJump=none', '-o', 'ControlMaster=no',
    '-o', 'ControlPath=none', '-o', 'ConnectTimeout=5', 'pilot.user@203.0.113.10',
    "node '/srv/query.js' 'list'"
  ]);
});
test('closed JSON rejects duplicate and escaped duplicate fields', t => {
  const f = fixture(t);
  for (const text of ['{"schema_version":1,"schema_version":1}',
    '{"schema_version":1,"schema_\\u0076ersion":1}']) {
    fs.writeFileSync(f.userConfig, text);
    assert.throws(() => ssh.readClosedJson(f.userConfig), fails('configuration_required'));
  }
});
test('service overrides and unknown fields fail before spawn', t => {
  const f = fixture(t); let calls = 0;
  const original = JSON.parse(fs.readFileSync(f.serviceConfig));
  for (const change of [{host: 'evil.example'}, {port: 23}, {remote_script: '/tmp/other'},
    {schema_version:1}, {probe_project_id: 'obsolete'}, {extra: true}]) {
    fs.writeFileSync(f.serviceConfig, JSON.stringify({...original, ...change}));
    assert.throws(() => ssh.executeQuery(f, 'list', [], {home: f.home,
      spawnSync: () => {calls++;}}), fails('host_identity_blocked'));
  }
  assert.equal(calls, 0);
});
test('known hosts drift fails before spawn', t => {
  const f = fixture(t); fs.appendFileSync(f.knownHosts, 'drift');
  assert.throws(() => ssh.executeQuery(f, 'list', [], {home: f.home,
    spawnSync: () => assert.fail('must not spawn')}), fails('host_identity_blocked'));
});
test('user fields injection and permissions fail before spawn', t => {
  const f = fixture(t);
  for (const change of [{ssh_user: 'pilot; whoami'}, {ssh_user: '-option'}, {extra: true}]) {
    fs.writeFileSync(f.userConfig, JSON.stringify({schema_version: 1, ssh_user: 'pilot.user',
      ssh_identity: f.identity, ...change}));
    assert.throws(() => ssh.executeQuery(f, 'list', [], {home: f.home,
      spawnSync: () => assert.fail('must not spawn')}), fails('configuration_required'));
  }
  fs.writeFileSync(f.userConfig, JSON.stringify({schema_version: 1, ssh_user: 'pilot.user', ssh_identity: f.identity}));
  fs.chmodSync(f.identity, 0o644);
  assert.throws(() => ssh.validateUserConfig(ssh.readClosedJson(f.userConfig), f.home), fails('configuration_required'));
});
test('identity symlink and outside SSH are rejected without reading key contents', t => {
  const f = fixture(t); const outside = path.join(f.home, 'outside');
  fs.writeFileSync(outside, 'fixture', {mode: 0o600});
  const link = path.join(f.home, '.ssh', 'link'); fs.symlinkSync(f.identity, link);
  for (const identity of [outside, link, 'relative', '/tmp/key\ncommand']) {
    assert.throws(() => ssh.validateUserConfig({schema_version: 1, ssh_user: 'pilot.user',
      ssh_identity: identity}, f.home), fails('configuration_required'));
  }
  const read = fs.readFileSync;
  fs.readFileSync = file => {assert.notEqual(file, f.identity); return read(file);};
  try {ssh.validateUserConfig({schema_version: 1, ssh_user: 'pilot.user', ssh_identity: f.identity}, f.home);}
  finally {fs.readFileSync = read;}
});
test('unsafe read and query enum reject before spawning', () => {
  for (const relative of ['../secret', '/etc/passwd', 'a//b', 'a/../b', '', 'a\\b', 'a\nb'])
    assert.throws(() => ssh.validateQuery('read', ['inventory', relative]), fails('invalid_query'));
  for (const project of ['-option', 'a;sh', '../repo', 'a'.repeat(129)])
    assert.throws(() => ssh.validateQuery('overview', [project]), fails('invalid_query'));
  assert.throws(() => ssh.validateQuery('shell', []), fails('invalid_query'));
  for (const limit of ['0', '51', '1;sh', '01'])
    assert.throws(() => ssh.validateQuery('search', ['term', '--project', 'inventory', '--limit', limit]), fails('invalid_query'));
});
test('remote parameter single quotes are strictly escaped', () => {
  assert.equal(ssh.shellQuote("a'b"), "'a'\\''b'");
  assert.equal(ssh.buildRemoteCommand('/srv/query.js', 'search',
    ["a'b", '--project', 'inventory', '--limit', '2']),
    "node '/srv/query.js' 'search' 'a'\\''b' '--project' 'inventory' '--limit' '2'");
});
test('stable SSH failure classification does not disclose stderr', () => {
  for (const [status, text, code] of [[255, 'REMOTE HOST IDENTIFICATION HAS CHANGED', 'host_identity_blocked'],
    [255, 'Permission denied (publickey) pilot.user fixture-key', 'authorization_required'],
    [255, 'Connection timed out', 'service_unavailable'], [1, 'node missing /secret', 'service_unavailable']]) {
    const error = ssh.classifySshFailure(status, text);
    assert.equal(error.code, code); assert.ok(!JSON.stringify(error.toJSON()).includes(text));
  }
});
test('spawn is bounded non-shell and output overflow/missing ssh fail closed', t => {
  const f = fixture(t);
  let options;
  const output = ssh.executeQuery(f, 'list', [], {home: f.home, spawnSync: (program, args, opts) => {
    assert.equal(program, 'ssh'); options = opts; return {status: 0, stdout: '{"success":true,"projects":{}}', stderr: ''};
  }});
  assert.equal(output, '{"success":true,"projects":{}}'); assert.equal(options.shell, false);
  assert.equal(options.timeout, 30000); assert.equal(options.maxBuffer, 4 * 1024 * 1024);
  for (const result of [{status: 0, stdout: 'x'.repeat(4*1024*1024+1)},
    {error: {code: 'ETIMEDOUT'}}, {error: {code: 'ENOENT'}}])
    assert.throws(() => ssh.executeQuery(f, 'list', [], {home: f.home, spawnSync: () => result}),
      fails(result.error?.code === 'ENOENT' ? 'configuration_required' : 'service_unavailable'));
});
test('list metadata is allowed but never returned in service Receipt', t => {
  const f = fixture(t); let calls = 0;
  const result = ssh.runProbe(f, {home:f.home, spawnSync:()=> {
    calls++; return {status:0, stdout:'{"success":true,"projects":{"synthetic":{"description":"fixture"}},"metadata":{"revision":2}}'};
  }});
  assert.equal(result.scope, 'service'); assert.equal(calls, 1);
  assert.ok(!JSON.stringify(result).includes('synthetic'));
});
test('service Doctor detects configuration drift after list', t => {
  const f = fixture(t);
  assert.throws(() => ssh.runProbe(f, {home:f.home, spawnSync:()=> {
    fs.writeFileSync(f.userConfig, '{}'); return {status:0, stdout:'{"success":true,"projects":{}}'};
  }}), fails('configuration_required'));
});
test('general query preserves service output and plain code without guessed Probe protocol', t => {
  const f = fixture(t);
  for (const stdout of ['{"success":true,"data":{"files":["page.js"]},"metadata":2}', 'export const Page = () => null;'])
    assert.equal(ssh.executeQuery(f, 'read', ['selected-project','page.js'], {home:f.home,
      spawnSync:()=>({status:0, stdout})}), stdout);
});
test('known-hosts option supports spaces but rejects config interpolation', () => {
  const args = ssh.buildSshArgs({host: '192.0.2.10', port: 22, knownHosts: '/tmp/with spaces/known_hosts',
    user: 'pilot.user', identity: '/tmp/key', remoteCommand: 'fixed'});
  assert.ok(args.includes('UserKnownHostsFile="/tmp/with spaces/known_hosts"'));
  assert.throws(() => ssh.buildSshArgs({host: '192.0.2.10', port: 22, knownHosts: '/tmp/%h/known_hosts',
    user: 'pilot.user', identity: '/tmp/key', remoteCommand: 'fixed'}), fails('host_identity_blocked'));
});
test('private SSH files cannot be read as JSON config and user configs require private permissions', t => {
  const f = fixture(t);
  assert.throws(() => ssh.readClosedJson(f.identity), fails('configuration_required'));
  fs.chmodSync(f.userConfig, 0o644);
  assert.throws(() => ssh.executeQuery(f, 'list', [], {home: f.home,
    spawnSync: () => assert.fail('must not spawn')}), fails('configuration_required'));
});
