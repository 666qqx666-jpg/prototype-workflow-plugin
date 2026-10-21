#!/usr/bin/env node
'use strict';
const ssh = require('../lib/ssh');
try {
  const args = process.argv.slice(2), options = {};
  for (const [flag, key] of [['--service-config', 'serviceConfig'], ['--known-hosts', 'knownHosts'], ['--user-config', 'userConfig'], ['--company-profile-sha256', 'companyProfileSha256']]) {
    if (args.shift() !== flag || !args[0] || args[0].startsWith('--')) throw new ssh.QueryError('invalid_query');
    options[key] = args.shift();
  }
  const command = args.shift();
  if (command === 'doctor') {
    if (args.length) throw new ssh.QueryError('invalid_query');
    console.log(JSON.stringify(ssh.runProbe(options)));
  } else process.stdout.write(ssh.executeQuery(options, command, args));
} catch (error) {
  const safe = error instanceof ssh.QueryError ? error : new ssh.QueryError('service_unavailable', 'internal_error');
  console.error(JSON.stringify(safe.toJSON()));
  process.exitCode = safe.code === 'configuration_required' ? 4 : 5;
}
