import path from 'path';

export function parseIntegerEnv(name, fallback, minimum = 0, env = process.env) {
  const rawValue = env[name];
  if (rawValue === undefined || rawValue === '') return fallback;

  const value = Number(rawValue);
  if (!Number.isInteger(value) || value < minimum) {
    throw new Error(`[配置错误] ${name} 必须是大于或等于 ${minimum} 的整数`);
  }
  return value;
}

export function parseBooleanEnv(name, fallback, env = process.env) {
  const rawValue = env[name];
  if (rawValue === undefined || rawValue === '') return fallback;

  const value = String(rawValue).trim().toLowerCase();
  if (value === 'true' || value === '1') return true;
  if (value === 'false' || value === '0') return false;
  throw new Error(`[配置错误] ${name} 必须是 true、false、1 或 0`);
}

export function parseEmailListEnv(name, env = process.env) {
  const values = (env[name] || '')
    .split(',')
    .map(value => value.trim())
    .filter(Boolean);
  const emailPattern = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
  if (values.some(value => !emailPattern.test(value))) {
    throw new Error(`[配置错误] ${name} 包含无效邮箱地址`);
  }
  return values;
}

const accountsFileSetting = process.env.ACCOUNTS_FILE?.trim()
  || '宝贝信息-114260514183055495.txt';
const ACCOUNTS_FILE = path.resolve(process.cwd(), accountsFileSetting);
const OUTPUT_DIR = path.join(process.cwd(), 'output');
const PROGRESS_FILE = path.join(OUTPUT_DIR, 'progress.json');
const RESULT_FILE = path.join(OUTPUT_DIR, 'result.txt');
const MANUAL_REVIEW_FILE = path.join(OUTPUT_DIR, 'manual-review.jsonl');

const HEADLESS = parseBooleanEnv('HEADLESS', true);
const SLOW_MO = parseIntegerEnv('SLOW_MO', 200);
const ACCOUNT_DELAY = parseIntegerEnv('ACCOUNT_DELAY', 5000);

const USER_DATA_DIR = path.join(process.cwd(), 'browser-data');

const RECOVERY_EMAIL_POOL = parseEmailListEnv('RECOVERY_EMAIL_POOL');
const ACCOUNTS_PER_RECOVERY_EMAIL = parseIntegerEnv('ACCOUNTS_PER_RECOVERY', 5, 1);

export {
  ACCOUNTS_FILE,
  OUTPUT_DIR,
  PROGRESS_FILE,
  RESULT_FILE,
  MANUAL_REVIEW_FILE,
  HEADLESS,
  SLOW_MO,
  ACCOUNT_DELAY,
  USER_DATA_DIR,
  RECOVERY_EMAIL_POOL,
  ACCOUNTS_PER_RECOVERY_EMAIL,
};
