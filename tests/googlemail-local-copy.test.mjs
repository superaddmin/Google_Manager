import assert from 'node:assert/strict';
import { existsSync } from 'node:fs';
import { mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import test from 'node:test';
import { fileURLToPath, pathToFileURL } from 'node:url';

const repositoryRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const googlemailRoot = join(repositoryRoot, 'googlemail');
const fixtureSecret = 'GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ';

function importGooglemailModule(relativePath) {
  return import(pathToFileURL(join(googlemailRoot, relativePath)).href);
}

test('vendored Googlemail snapshot contains its declared interface', async () => {
  const requiredPaths = [
    'package.json',
    'package-lock.json',
    'src/account-parser.mjs',
    'src/config.mjs',
    'src/totp.mjs',
    'src/google-automator.mjs',
    'tests/totp.test.mjs',
    'docs/README.md',
  ];

  for (const relativePath of requiredPaths) {
    assert.equal(existsSync(join(googlemailRoot, relativePath)), true, relativePath);
  }

  const packageJson = JSON.parse(
    await readFile(join(googlemailRoot, 'package.json'), 'utf8'),
  );
  assert.equal(packageJson.name, 'google-2fa-tool');
  assert.equal(packageJson.type, 'module');
});

test('vendored Googlemail keeps runtime and credential paths ignored', async () => {
  const gitignore = await readFile(join(googlemailRoot, '.gitignore'), 'utf8');
  for (const pattern of ['node_modules/', 'browser-data/', 'output/', 'coverage/', '.env']) {
    assert.equal(gitignore.includes(pattern), true, pattern);
  }
});

test('vendored ESM modules load on Windows and expose working interfaces', async () => {
  const totp = await importGooglemailModule('src/totp.mjs');
  const accountParser = await importGooglemailModule('src/account-parser.mjs');

  assert.match(totp.generateTOTP(fixtureSecret), /^\d{6}$/);
  assert.equal(typeof accountParser.parseAccounts, 'function');

  const temporaryDirectory = await mkdtemp(join(tmpdir(), 'googlemail-copy-'));
  try {
    const accountsFile = join(temporaryDirectory, 'accounts.txt');
    await writeFile(
      accountsFile,
      `fixture@example.com----fixture-password----recovery@example.com----${fixtureSecret}\n`,
      'utf8',
    );

    const accounts = accountParser.parseAccounts(accountsFile);
    assert.equal(accounts.length, 1);
    assert.equal(accounts[0].email, 'fixture@example.com');
  } finally {
    await rm(temporaryDirectory, { recursive: true, force: true });
  }
});
