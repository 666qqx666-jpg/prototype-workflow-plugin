import {readFileSync} from 'node:fs';
import {resolve} from 'node:path';
import {validateManifest} from '../src/reader/contract.mjs';

const args = process.argv.slice(2);
const manifestArg = args.length === 2 && args[0] === '--manifest' ? args[1] : null;
if (!manifestArg) {
  process.stderr.write('Usage: node scripts/validate-manifest.mjs --manifest <manifest.json>\n');
  process.exit(1);
}

try {
  const result = validateManifest(JSON.parse(readFileSync(resolve(manifestArg), 'utf8')));
  for (const warning of result.warnings) process.stdout.write(`WARN ${warning.code} ${warning.path}\n`);
  for (const error of result.errors) process.stderr.write(`FAIL ${error.code} ${error.path}\n`);
  if (!result.ok) process.exit(1);
  process.stdout.write(`PASS valid element-card manifest: ${result.summary.cards} cards\n`);
  process.stdout.write(`PASS ${result.summary.pages} pages\n`);
  process.stdout.write('PASS PRD source closure\n');
  process.stdout.write('PASS no API mapping\n');
} catch (error) {
  process.stderr.write(`FAIL manifest parse: ${error.message}\n`);
  process.exit(1);
}
