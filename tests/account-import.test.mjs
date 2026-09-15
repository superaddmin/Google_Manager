import assert from 'node:assert/strict';
import test from 'node:test';
import { parseAccountImport } from '../frontend/src/utils/accountImport.js';

const fixtureAccount = {
  email: 'fixture@example.test',
  password: 'FixturePassword',
  recovery: 'recovery@example.test',
  secret: 'JBSWY3DPEHPK3PXP',
  remark: 'UnitedStates',
};

for (const delimiter of ['|', '——', '----', '--']) {
  test(`parses all five fields separated by ${delimiter}`, () => {
    const result = parseAccountImport(Object.values(fixtureAccount).join(delimiter));
    assert.deepEqual(result, { accounts: [fixtureAccount], errors: [] });
  });
}

test('parses six pipe-separated accounts and Windows newlines', () => {
  const accounts = Array.from({ length: 6 }, (unused, index) => ({
    ...fixtureAccount,
    email: `fixture-${index}@example.test`,
  }));
  const text = accounts.map(account => Object.values(account).join('|')).join('\r\n');
  assert.deepEqual(parseAccountImport(text), { accounts, errors: [] });
});

test('detects delimiters at the email boundary rather than inside passwords', () => {
  for (const [delimiter, password] of [
    ['|', 'Password----with--dashes——'],
    ['----', 'Password——with|symbols'],
    ['——', 'Password|with----dashes'],
    ['--', 'Password|with——symbols'],
  ]) {
    const account = { ...fixtureAccount, password };
    assert.deepEqual(parseAccountImport(Object.values(account).join(delimiter)), {
      accounts: [account], errors: [],
    });
  }
});

test('allows different row formats, blank lines and omitted optional fields', () => {
  const text = '\nfirst@example.test|FirstPassword\r\n\r\nsecond@example.test----SecondPassword\r';
  const result = parseAccountImport(text);
  assert.equal(result.accounts.length, 2);
  assert.deepEqual(result.errors, []);
  assert.deepEqual(result.accounts[0], {
    email: 'first@example.test', password: 'FirstPassword', recovery: '', secret: '', remark: '',
  });
});

test('preserves password whitespace and removes whitespace only from the TOTP field', () => {
  const account = { ...fixtureAccount, password: '  FixturePassword  ' };
  const text = `  ${account.email}|${account.password}| ${account.recovery} |JBSW Y3DP\tEHPK 3PXP| UnitedStates `;
  assert.deepEqual(parseAccountImport(text), { accounts: [account], errors: [] });
});

test('preserves delimiters inside the remark', () => {
  const account = { ...fixtureAccount, remark: 'UnitedStates|additional note' };
  assert.deepEqual(parseAccountImport(Object.values(account).join('|')), { accounts: [account], errors: [] });
});

test('reports original line numbers without echoing credentials', () => {
  const text = '\ninvalid@example.test,PRIVATE_FIXTURE\nmissing@example.test|  \ninvalid-recovery@example.test|Password|invalid\nvalid@example.test--Password';
  const result = parseAccountImport(text);
  assert.deepEqual(result.errors.map(error => error.lineNumber), [2, 3, 4]);
  assert.equal(result.accounts.length, 1);
  assert.equal(JSON.stringify(result.errors).includes('PRIVATE_FIXTURE'), false);
});

test('rejects invalid email addresses and missing separators', () => {
  for (const text of [
    'not-an-email|Password',
    'missing@example.test',
    'name@@example.test|Password',
    'invalid@example.test,Fixture--Password,recovery@example.test',
  ]) {
    const result = parseAccountImport(text);
    assert.equal(result.accounts.length, 0);
    assert.equal(result.errors.length, 1);
  }
});

test('uses the validated email boundary when its local part contains delimiter characters', () => {
  const account = { ...fixtureAccount, email: 'first----last@example.test' };
  assert.deepEqual(parseAccountImport(Object.values(account).join('----')), {
    accounts: [account], errors: [],
  });
});

test('returns no errors or accounts for blank input', () => {
  assert.deepEqual(parseAccountImport(' \n\t\r\n'), { accounts: [], errors: [] });
});
