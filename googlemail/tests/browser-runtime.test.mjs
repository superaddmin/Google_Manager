import path from 'node:path';
import { describe, expect, it } from 'vitest';
import {
  EXPECTED_CHROME_VERSION,
  assertExpectedChromeVersion,
  cleanupBrowserStartup,
  getBrowserLaunchOptions,
} from '../src/browser-runtime.mjs';

const absoluteExecutablePath = path.resolve('synthetic-chrome', 'chrome');

describe('Browser runtime configuration', () => {
  it('allows the Playwright-managed browser only outside production', () => {
    expect(getBrowserLaunchOptions({ headless: true }, {})).toEqual({
      headless: true,
      chromiumSandbox: true,
    });
  });

  it('requires an explicit browser executable in production', () => {
    expect(() => getBrowserLaunchOptions({}, { FLASK_ENV: 'production' }))
      .toThrow('GOOGLE_MANAGER_CHROME_EXECUTABLE_PATH is required');
  });

  it('accepts an absolute executable path and does not silently fall back', () => {
    const options = getBrowserLaunchOptions(
      { headless: true },
      {
        FLASK_ENV: 'production',
        GOOGLE_MANAGER_CHROME_EXECUTABLE_PATH: absoluteExecutablePath,
      },
    );

    expect(options).toEqual({
      headless: true,
      chromiumSandbox: true,
      executablePath: absoluteExecutablePath,
    });
  });

  it('rejects relative or control-character executable paths', () => {
    for (const executablePath of ['relative/chrome', `${absoluteExecutablePath}\nignored`]) {
      expect(() => getBrowserLaunchOptions({}, {
        GOOGLE_MANAGER_CHROME_EXECUTABLE_PATH: executablePath,
      })).toThrow('GOOGLE_MANAGER_CHROME_EXECUTABLE_PATH');
    }
  });

  it('rejects a channel conflict instead of choosing a browser implicitly', () => {
    expect(() => getBrowserLaunchOptions({}, {
      GOOGLE_MANAGER_CHROME_EXECUTABLE_PATH: absoluteExecutablePath,
      CHROME_CHANNEL: 'chrome',
    })).toThrow('cannot be combined');
  });

  it('keeps the local channel fallback outside production', () => {
    expect(getBrowserLaunchOptions({}, { CHROME_CHANNEL: 'chrome' })).toEqual({
      chromiumSandbox: true,
      channel: 'chrome',
    });
  });

  it('prevents callers from disabling or bypassing the shared sandbox policy', () => {
    for (const options of [
      { chromiumSandbox: false },
      { executablePath: absoluteExecutablePath },
      { channel: 'chrome' },
      { args: ['--no-sandbox'] },
      { args: ['--disable-setuid-sandbox=true'] },
    ]) {
      expect(() => getBrowserLaunchOptions(options, {})).toThrow();
    }
  });

  it('requires the exact approved Chrome version in production', () => {
    const environment = { FLASK_ENV: 'production' };
    expect(() => assertExpectedChromeVersion('153.0.8010.12', environment))
      .toThrow(EXPECTED_CHROME_VERSION);
    expect(() => assertExpectedChromeVersion('', environment))
      .toThrow(EXPECTED_CHROME_VERSION);
    expect(() => assertExpectedChromeVersion(EXPECTED_CHROME_VERSION, environment))
      .not.toThrow();
  });

  it('does not pin the developer fallback browser version', () => {
    expect(() => assertExpectedChromeVersion('developer-version', {})).not.toThrow();
  });

  it('attempts every cleanup step and reports only fixed resource labels', async () => {
    const sensitiveFailure = new Error('secret-profile-path');
    const persistentContext = { close: vi.fn().mockRejectedValue(sensitiveFailure) };
    const browserContext = { close: vi.fn().mockResolvedValue() };
    const browser = { close: vi.fn().mockRejectedValue(sensitiveFailure) };
    const removeDirectory = vi.fn().mockRejectedValue(sensitiveFailure);

    let cleanupError;
    try {
      await cleanupBrowserStartup(
        {
          persistentContext,
          browserContext,
          browser,
          temporaryProfile: absoluteExecutablePath,
        },
        removeDirectory,
      );
    } catch (error) {
      cleanupError = error;
    }

    expect(persistentContext.close).toHaveBeenCalledOnce();
    expect(browserContext.close).toHaveBeenCalledOnce();
    expect(browser.close).toHaveBeenCalledOnce();
    expect(removeDirectory).toHaveBeenCalledWith(
      absoluteExecutablePath,
      { recursive: true, force: true },
    );
    expect(cleanupError?.message).toContain('persistentContext');
    expect(cleanupError?.message).toContain('browser');
    expect(cleanupError?.message).toContain('temporaryProfile');
    expect(cleanupError?.message).not.toContain('secret-profile-path');
  });
});
