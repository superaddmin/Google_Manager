import { describe, expect, it, vi } from 'vitest';
import { setupNewAuthenticator } from '../src/google-automator.mjs';

const FIXTURE_SECRET = 'GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ';

function createPage({ hasSecretInput = true, hasCodeInput = true, hasVerifyButton = true } = {}) {
  const secretInput = hasSecretInput ? { fill: vi.fn(async () => {}) } : null;
  const codeInput = hasCodeInput
    ? {
        fill: vi.fn(async () => {}),
        isVisible: vi.fn(async () => false),
      }
    : null;

  return {
    waitForTimeout: vi.fn(async () => {}),
    screenshot: vi.fn(async () => {}),
    click: vi.fn(async () => {}),
    isVisible: vi.fn(async selector => (
      hasVerifyButton && selector === 'button:has-text("验证")'
    )),
    $: vi.fn(async selector => {
      if (selector.includes('secret') || selector.includes('密钥') || selector.includes('type="text"')) {
        return secretInput;
      }
      if (selector.includes('code') || selector.includes('验证码') || selector.includes('type="tel"')) {
        return codeInput;
      }
      return null;
    }),
  };
}

describe('New authenticator setup', () => {
  it('should require manual review when the secret input is missing', async () => {
    const result = await setupNewAuthenticator(
      createPage({ hasSecretInput: false }),
      vi.fn(),
      () => FIXTURE_SECRET,
    );

    expect(result).toMatchObject({ success: false, requiresManualReview: true });
  });

  it('should require manual review when the verification input is missing', async () => {
    const result = await setupNewAuthenticator(
      createPage({ hasCodeInput: false }),
      vi.fn(),
      () => FIXTURE_SECRET,
    );

    expect(result).toMatchObject({ success: false, requiresManualReview: true });
  });

  it('should not report success without a verification submit button', async () => {
    const result = await setupNewAuthenticator(
      createPage({ hasVerifyButton: false }),
      vi.fn(),
      () => FIXTURE_SECRET,
    );

    expect(result).toMatchObject({ success: false, requiresManualReview: true });
  });

  it('should return the generated secret after confirmed submission', async () => {
    const result = await setupNewAuthenticator(
      createPage(),
      vi.fn(),
      () => FIXTURE_SECRET,
    );

    expect(result).toEqual({ success: true, newSecret: FIXTURE_SECRET });
  });
});
