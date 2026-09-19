/**
 * 批量 OAuth 2.0 自动授权命令行工作入口
 *
 * 由 Python 后端 BatchOAuthManager 子进程拉起执行：
 * 读取任务文件，驱动 Playwright 无头浏览器依次/按批次完成各账号的 Google OAuth 授权
 */

import { chromium } from 'playwright';
import fs from 'fs';
import path from 'path';
import * as config from './config.mjs';
import { authorizeGoogleOAuth } from './oauth-authorizer.mjs';
import { maskEmail, redactSensitiveText } from './redaction.mjs';

function emitEvent(event, data = {}) {
  const payload = {
    event,
    timestamp: new Date().toISOString(),
    ...data,
  };
  console.log(`__OAUTH_EVENT__${JSON.stringify(payload)}`);
}

async function main() {
  const tasksFile = process.env.OAUTH_TASKS_FILE;
  if (!tasksFile || !fs.existsSync(tasksFile)) {
    console.error(`[错误] 找不到批量 OAuth 任务文件: ${tasksFile}`);
    process.exitCode = 1;
    return;
  }

  let tasks = [];
  try {
    const content = fs.readFileSync(tasksFile, 'utf-8');
    tasks = JSON.parse(content);
  } catch (err) {
    console.error(`[错误] 解析任务文件失败: ${err.message}`);
    process.exitCode = 1;
    return;
  }

  if (!Array.isArray(tasks) || tasks.length === 0) {
    console.log('[提示] 待授权任务列表为空');
    emitEvent('batch_finished', { total: 0, completed: 0, failed: 0 });
    return;
  }

  const outputDir = config.OUTPUT_DIR;
  if (!fs.existsSync(outputDir)) {
    fs.mkdirSync(outputDir, { recursive: true });
  }

  const progressFile = path.join(outputDir, 'oauth-progress.json');
  const resultsFile = path.join(outputDir, 'oauth-results.jsonl');

  const proxy = process.env.PROXY ? { server: process.env.PROXY } : undefined;
  const headless = config.HEADLESS;
  const slowMo = config.SLOW_MO;
  const accountDelay = config.ACCOUNT_DELAY;

  console.log('═'.repeat(60));
  console.log('  Google 批量 OAuth 2.0 自动授权任务启动');
  console.log('═'.repeat(60));
  console.log(`  待处理账号数: ${tasks.length}`);
  console.log(`  Headless 模式: ${headless}`);
  console.log(`  SlowMo: ${slowMo}ms`);
  console.log(`  账号间隔: ${accountDelay}ms`);
  if (proxy) {
    console.log(`  代理服务: ${proxy.server}`);
  }
  console.log('═'.repeat(60));

  emitEvent('batch_started', { total: tasks.length });

  let completedCount = 0;
  let failedCount = 0;

  const browser = await chromium.launch({
    headless,
    slowMo,
    proxy,
    args: [
      '--disable-blink-features=AutomationControlled',
      '--disable-features=TranslateUI',
      '--no-sandbox',
      '--disable-dev-shm-usage',
      '--disable-setuid-sandbox',
      '--lang=zh-CN',
    ],
  });

  try {
    for (let i = 0; i < tasks.length; i++) {
      const task = tasks[i];
      const { accountId, email, password, secret, recovery, authUrl } = task;

      console.log(`\n[${i + 1}/${tasks.length}] 开始授权: ${maskEmail(email)} (ID: ${accountId})`);
      emitEvent('account_started', { accountId, email: maskEmail(email), index: i });

      // 每个账号使用全新的独立上下文，隔绝 cookies/cache 避免串号
      const context = await browser.newContext({
        viewport: { width: 1280, height: 900 },
        locale: 'zh-CN',
        timezoneId: 'Asia/Shanghai',
        userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36',
      });

      const page = await context.newPage();
      await page.setExtraHTTPHeaders({ 'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8' });

      function logger(msg) {
        const line = redactSensitiveText(msg, [password, secret]);
        console.log(`  ${line}`);
        emitEvent('log', { accountId, message: line });
      }

      let result = null;
      try {
        result = await authorizeGoogleOAuth(page, { email, password, secret, recovery }, authUrl, logger);
      } catch (err) {
        result = { success: false, error: err.message };
      } finally {
        await context.close().catch(() => {});
      }

      const resultRecord = {
        accountId,
        email,
        success: Boolean(result?.success),
        error: result?.error || null,
        screenshot: result?.screenshot || null,
        finishedAt: new Date().toISOString(),
      };

      fs.appendFileSync(resultsFile, JSON.stringify(resultRecord) + '\n');

      if (result?.success) {
        completedCount++;
        emitEvent('account_completed', { accountId, email: maskEmail(email), success: true });
      } else {
        failedCount++;
        emitEvent('account_completed', {
          accountId,
          email: maskEmail(email),
          success: false,
          error: result?.error,
        });
      }

      const progressData = {
        total: tasks.length,
        completed: completedCount,
        failed: failedCount,
        currentIndex: i + 1,
        lastUpdated: new Date().toISOString(),
      };
      fs.writeFileSync(progressFile, JSON.stringify(progressData, null, 2));

      if (i < tasks.length - 1 && accountDelay > 0) {
        console.log(`  [等待] 账号间隔等待 ${accountDelay / 1000}s...`);
        await new Promise(resolve => setTimeout(resolve, accountDelay));
      }
    }
  } finally {
    await browser.close().catch(() => {});
  }

  console.log('\n' + '═'.repeat(60));
  console.log(`  批量授权任务执行完毕: 成功 ${completedCount}，失败 ${failedCount}`);
  console.log('═'.repeat(60));

  emitEvent('batch_finished', {
    total: tasks.length,
    completed: completedCount,
    failed: failedCount,
  });
}

main().catch(err => {
  console.error(`[致命错误] 批量授权执行失败: ${err.message}`);
  process.exitCode = 1;
});
