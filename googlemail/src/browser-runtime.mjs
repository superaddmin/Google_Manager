import path from 'node:path';

export const EXPECTED_CHROME_VERSION = '153.0.8010.52';

const RESERVED_OPTIONS = new Set([
  'channel',
  'chromiumSandbox',
  'executablePath',
]);
const UNSAFE_SANDBOX_ARGUMENTS = new Set([
  '--disable-setuid-sandbox',
  '--no-sandbox',
]);

function readEnvironmentValue(environment, name) {
  const rawValue = environment?.[name];
  if (rawValue === undefined || rawValue === null || rawValue === '') return undefined;
  if (typeof rawValue !== 'string') {
    throw new TypeError(`${name} must be a string`);
  }

  const value = rawValue.trim();
  if (!value) return undefined;
  if (/[\0\r\n]/u.test(value)) {
    throw new Error(`${name} contains invalid control characters`);
  }
  return value;
}

function isProduction(environment) {
  return readEnvironmentValue(environment, 'FLASK_ENV')?.toLowerCase() === 'production';
}

function validateCallerOptions(options) {
  if (!options || typeof options !== 'object' || Array.isArray(options)) {
    throw new TypeError('Browser launch options must be an object');
  }

  for (const name of RESERVED_OPTIONS) {
    if (Object.hasOwn(options, name)) {
      throw new Error(`${name} is managed by browser-runtime.mjs`);
    }
  }

  if (options.args !== undefined && !Array.isArray(options.args)) {
    throw new TypeError('Browser launch args must be an array');
  }
  for (const argument of options.args || []) {
    if (typeof argument !== 'string') {
      throw new TypeError('Browser launch args must contain only strings');
    }
    const flagName = argument.split('=', 1)[0].toLowerCase();
    if (UNSAFE_SANDBOX_ARGUMENTS.has(flagName)) {
      throw new Error(`Unsafe Chromium sandbox argument is forbidden: ${flagName}`);
    }
  }
}

export function getBrowserLaunchOptions(options = {}, environment = process.env) {
  validateCallerOptions(options);

  const executablePath = readEnvironmentValue(
    environment,
    'GOOGLE_MANAGER_CHROME_EXECUTABLE_PATH',
  );
  const channel = readEnvironmentValue(environment, 'CHROME_CHANNEL');

  if (executablePath && channel) {
    throw new Error(
      'GOOGLE_MANAGER_CHROME_EXECUTABLE_PATH cannot be combined with CHROME_CHANNEL',
    );
  }
  if (executablePath && !path.isAbsolute(executablePath)) {
    throw new Error('GOOGLE_MANAGER_CHROME_EXECUTABLE_PATH must be an absolute path');
  }
  if (isProduction(environment) && !executablePath) {
    throw new Error(
      'GOOGLE_MANAGER_CHROME_EXECUTABLE_PATH is required when FLASK_ENV=production',
    );
  }

  return {
    ...options,
    ...(options.args ? { args: [...options.args] } : {}),
    chromiumSandbox: true,
    ...(executablePath ? { executablePath } : {}),
    ...(!executablePath && channel ? { channel } : {}),
  };
}

export function assertExpectedChromeVersion(actualVersion, environment = process.env) {
  if (!isProduction(environment)) return;
  if (actualVersion !== EXPECTED_CHROME_VERSION) {
    throw new Error(
      `Production Chrome version must be ${EXPECTED_CHROME_VERSION}; received ${actualVersion || '<empty>'}`,
    );
  }
}

export async function cleanupBrowserStartup(
  { persistentContext, browserContext, browser, temporaryProfile },
  removeDirectory,
) {
  const failures = [];
  for (const [name, resource] of [
    ['persistentContext', persistentContext],
    ['browserContext', browserContext],
    ['browser', browser],
  ]) {
    if (!resource) continue;
    try {
      await resource.close();
    } catch {
      failures.push(name);
    }
  }

  if (temporaryProfile) {
    try {
      await removeDirectory(temporaryProfile, { recursive: true, force: true });
    } catch {
      failures.push('temporaryProfile');
    }
  }

  if (failures.length > 0) {
    throw new Error(`Browser startup cleanup failed: ${failures.join(', ')}`);
  }
}
