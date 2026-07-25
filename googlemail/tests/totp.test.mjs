import { describe, it, expect } from 'vitest';
import {
  generateTOTP,
  generateBase32Secret,
  getBase32DecodedByteLength,
  verifyTOTP,
  getTOTPTimeRemaining,
} from '../src/totp.mjs';

describe('TOTP Module', () => {
  describe('generateBase32Secret', () => {
    it('should generate a Base32 secret with default 20 bytes', () => {
      const secret = generateBase32Secret();
      expect(secret).toBeDefined();
      expect(typeof secret).toBe('string');
      expect(secret.length).toBeGreaterThanOrEqual(32);
      expect(/^[A-Z2-7]+$/.test(secret)).toBe(true);
    });

    it('should generate a Base32 secret with specified bytes', () => {
      const secret = generateBase32Secret(10);
      expect(secret).toBeDefined();
      expect(typeof secret).toBe('string');
      expect(secret.length).toBeGreaterThanOrEqual(16);
      expect(/^[A-Z2-7]+$/.test(secret)).toBe(true);
    });

    it('should generate different secrets each time', () => {
      const secret1 = generateBase32Secret();
      const secret2 = generateBase32Secret();
      expect(secret1).not.toBe(secret2);
    });

    it('should reject invalid byte lengths', () => {
      expect(() => generateBase32Secret(0)).toThrow(RangeError);
      expect(() => generateBase32Secret(-1)).toThrow(RangeError);
      expect(() => generateBase32Secret(1.5)).toThrow(RangeError);
      expect(() => generateBase32Secret(Buffer.alloc(0))).toThrow(RangeError);
    });

    it('should report the decoded Base32 byte length', () => {
      expect(getBase32DecodedByteLength('JBSWY3DPEHPK3PXP')).toBe(10);
      expect(getBase32DecodedByteLength('GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ')).toBe(20);
      expect(getBase32DecodedByteLength('invalid-secret')).toBe(0);
    });
  });

  describe('generateTOTP', () => {
    it('should generate a 6-digit TOTP code', () => {
      const secret = generateBase32Secret();
      const totp = generateTOTP(secret);
      expect(totp).toBeDefined();
      expect(typeof totp).toBe('string');
      expect(totp.length).toBe(6);
      expect(/^\d{6}$/.test(totp)).toBe(true);
    });

    it('should match the RFC 6238 SHA1 vector at a fixed epoch', () => {
      const secret = 'GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ';
      expect(generateTOTP(secret, 59)).toBe('287082');
    });

    it('should reject Base32 secrets shorter than 16 decoded bytes', () => {
      expect(generateTOTP('JBSWY3DPEHPK3PXP')).toBeNull();
    });

    it('should return null for invalid secret', () => {
      const result = generateTOTP('invalid-secret-with-special-chars!@#');
      expect(result).toBeNull();
    });

    it('should return null for non-string secrets', () => {
      expect(generateTOTP(null)).toBeNull();
    });

    it('should handle whitespace in secret', () => {
      const secret = generateBase32Secret();
      const totpWithSpace = generateTOTP('  ' + secret + '  ');
      const totpWithoutSpace = generateTOTP(secret);
      expect(totpWithSpace).toBe(totpWithoutSpace);
    });
  });

  describe('verifyTOTP', () => {
    it('should verify a valid TOTP code', () => {
      const secret = generateBase32Secret();
      const totp = generateTOTP(secret);
      expect(totp).not.toBeNull();
      const verified = verifyTOTP(totp, secret);
      expect(verified).toBe(true);
    });

    it('should reject an invalid TOTP code', () => {
      const secret = generateBase32Secret();
      const verified = verifyTOTP('123456', secret);
      expect(verified).toBe(false);
    });

    it('should handle whitespace in secret during verification', () => {
      const secret = generateBase32Secret();
      const totp = generateTOTP(secret);
      const verified = verifyTOTP(totp, '  ' + secret + '  ');
      expect(verified).toBe(true);
    });

    it('should return false for invalid secret', () => {
      const verified = verifyTOTP('123456', 'invalid');
      expect(verified).toBe(false);
    });

    it('should reject malformed token input', () => {
      const secret = generateBase32Secret();
      expect(verifyTOTP('12345', secret)).toBe(false);
      expect(verifyTOTP(null, secret)).toBe(false);
    });
  });

  describe('getTOTPTimeRemaining', () => {
    it('should return a number between 0 and 30', () => {
      const remaining = getTOTPTimeRemaining();
      expect(typeof remaining).toBe('number');
      expect(remaining).toBeGreaterThan(0);
      expect(remaining).toBeLessThanOrEqual(30);
    });
  });
});
