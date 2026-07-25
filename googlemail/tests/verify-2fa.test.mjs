import { afterEach, describe, expect, it } from 'vitest';
import { spawnSync } from 'child_process';
import fs from 'fs';
import os from 'os';
import path from 'path';

describe('Offline 2FA verification CLI', () => {
  let tempDir;

  afterEach(() => {
    if (tempDir && fs.existsSync(tempDir)) {
      fs.rmSync(tempDir, { recursive: true, force: true });
    }
  });

  it('should always redact account and TOTP data', () => {
    tempDir = fs.mkdtempSync(path.join(os.tmpdir(), 'googlemail-verify-2fa-'));
    const accountsFile = path.join(tempDir, 'accounts.txt');
    const email = 'user@example.com';
    const secret = 'GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ';
    fs.writeFileSync(
      accountsFile,
      `${email}----<PASSWORD>----recovery@example.com----${secret}`,
      'utf8',
    );

    const result = spawnSync(process.execPath, ['src/verify-2fa.mjs'], {
      cwd: process.cwd(),
      encoding: 'utf8',
      env: { ...process.env, ACCOUNTS_FILE: accountsFile },
    });

    expect(result.status).toBe(0);
    expect(result.stdout).toContain('us***@example.com');
    expect(result.stdout).toContain('<TOTP_CODE>');
    expect(result.stdout).not.toContain(email);
    expect(result.stdout).not.toContain(secret);
  });
});
