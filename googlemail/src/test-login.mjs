import { chromium } from 'playwright';
import { parseAccounts } from './account-parser.mjs';
import { loginAndChange2FA } from './google-automator.mjs';
import * as config from './config.mjs';
import { maskEmail, redactSensitiveText } from './redaction.mjs';
import fs from 'fs';

const accounts = parseAccounts(config.ACCOUNTS_FILE);

function redactResult(result) {
  if (!result || typeof result !== 'object') return result;
  const redacted = { ...result };
  if (redacted.newSecret) redacted.newSecret = '<BASE32_SECRET>';
  if (redacted.error) {
    redacted.error = String(redacted.error)
      .replace(/\b\d{6}\b/g, '<TOTP_CODE>')
      .replace(/[A-Z2-7]{16,}={0,6}/g, '<BASE32_SECRET>');
  }
  return redacted;
}

const testEmail = process.env.TEST_EMAIL || accounts[0]?.email;
const account = accounts.find(a => a.email === testEmail);

if (!account) {
  console.error(`未找到测试账号: ${maskEmail(testEmail)}`);
  console.log(`可用账号数量: ${accounts.length}`);
  process.exit(1);
}

console.log(`测试账号: ${maskEmail(account.email)}`);
const testRecoveryEmail = process.env.TEST_RECOVERY_EMAIL || config.RECOVERY_EMAIL_POOL[0] || null;
if (testRecoveryEmail) {
  console.log(`目标恢复邮箱: ${maskEmail(testRecoveryEmail)}`);
}

if (!fs.existsSync(config.USER_DATA_DIR)) {
  fs.mkdirSync(config.USER_DATA_DIR, { recursive: true });
}
if (!fs.existsSync(config.OUTPUT_DIR)) {
  fs.mkdirSync(config.OUTPUT_DIR, { recursive: true });
}

let context = null;
let page = null;

async function cleanup() {
  if (page) {
    try {
      await page.close();
    } catch {}
  }
  if (context) {
    try {
      await context.close();
      console.log('\n[清理] 浏览器上下文已关闭');
    } catch {}
  }
}

process.on('SIGINT', async () => {
  console.log('\n收到 Ctrl+C，正在关闭浏览器...');
  await cleanup();
  process.exit(0);
});

process.on('SIGTERM', async () => {
  console.log('\n收到终止信号，正在关闭浏览器...');
  await cleanup();
  process.exit(0);
});

try {
  context = await chromium.launchPersistentContext(config.USER_DATA_DIR, {
    headless: false,
    slowMo: 300,
    viewport: { width: 1280, height: 900 },
    locale: 'zh-CN',
    args: [
      '--disable-blink-features=AutomationControlled',
      '--disable-features=TranslateUI',
      '--lang=zh-CN',
    ],
  });

  page = await context.newPage();
  await page.setExtraHTTPHeaders({ 'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8' });

  const log = message => console.log(redactSensitiveText(message, [
    account.password,
    account.oldSecret,
  ]));
  const result = await loginAndChange2FA(page, account, log, testRecoveryEmail);

  console.log('\n结果:', JSON.stringify(redactResult(result), null, 2));
  console.log('浏览器保持打开，可手动检查后按 Ctrl+C 退出');

  await new Promise(() => {});
} catch (err) {
  console.error('测试过程中发生异常:', redactSensitiveText(err.message, [
    account.password,
    account.oldSecret,
  ]));
  console.error('堆栈:', redactSensitiveText(err.stack, [account.password, account.oldSecret]));
  await cleanup();
  process.exit(1);
}
