import { describe, expect, it } from 'vitest';
import { maskEmail, redactSensitiveText } from '../src/redaction.mjs';

describe('Sensitive data redaction', () => {
  it('should mask email addresses', () => {
    expect(maskEmail('user@example.com')).toBe('us***@example.com');
    expect(maskEmail('x@example.com')).toBe('x***@example.com');
    expect(maskEmail('invalid')).toBe('<EMAIL>');
  });

  it('should redact emails, TOTP codes, Base32 secrets, and known values', () => {
    const output = redactSensitiveText(
      'user@example.com <PASSWORD> JBSWY3DPEHPK3PXP 123456',
      ['<PASSWORD>'],
    );

    expect(output).toBe('us***@example.com <REDACTED> <BASE32_SECRET> <TOTP_CODE>');
  });
});
