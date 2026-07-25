import { describe, expect, it } from 'vitest';
import { parseBooleanEnv, parseEmailListEnv, parseIntegerEnv } from '../src/config.mjs';

describe('Configuration parsing', () => {
  it('should parse valid integer settings', () => {
    expect(parseIntegerEnv('DELAY', 100, 0, { DELAY: '250' })).toBe(250);
    expect(parseIntegerEnv('DELAY', 100, 0, {})).toBe(100);
  });

  it('should reject invalid or out-of-range integer settings', () => {
    expect(() => parseIntegerEnv('DELAY', 100, 0, { DELAY: '12ms' }))
      .toThrow('DELAY');
    expect(() => parseIntegerEnv('GROUP_SIZE', 5, 1, { GROUP_SIZE: '0' }))
      .toThrow('GROUP_SIZE');
  });

  it('should parse boolean settings case-insensitively', () => {
    expect(parseBooleanEnv('HEADLESS', true, { HEADLESS: 'FALSE' })).toBe(false);
    expect(parseBooleanEnv('HEADLESS', false, { HEADLESS: '1' })).toBe(true);
    expect(parseBooleanEnv('HEADLESS', true, {})).toBe(true);
  });

  it('should reject unknown boolean values', () => {
    expect(() => parseBooleanEnv('HEADLESS', true, { HEADLESS: 'sometimes' }))
      .toThrow('HEADLESS');
  });

  it('should validate the recovery email pool without exposing invalid values', () => {
    expect(parseEmailListEnv('RECOVERY_EMAIL_POOL', {
      RECOVERY_EMAIL_POOL: 'first@example.com, second@example.com',
    })).toEqual(['first@example.com', 'second@example.com']);
    expect(() => parseEmailListEnv('RECOVERY_EMAIL_POOL', {
      RECOVERY_EMAIL_POOL: 'not-an-email',
    })).toThrow('RECOVERY_EMAIL_POOL');
  });
});
