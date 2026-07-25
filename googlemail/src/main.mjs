import { chromium } from 'playwright';
import * as config from './config.mjs';
import { parseAccounts, loadProgress, saveProgress, appendResult } from './account-parser.mjs';
import { loginAndChange2FA } from './google-automator.mjs';
import { maskEmail, redactSensitiveText } from './redaction.mjs';
import path from 'path';
import fs from 'fs';

async function cleanupOldScreenshots(maxKeep = 10) {
  try {
    const outputDir = config.OUTPUT_DIR;
    if (!fs.existsSync(outputDir)) return;

    const files = await fs.promises.readdir(outputDir);
    const screenshots = await Promise.all(files
      .filter(f => f.startsWith('debug-') && (f.endsWith('.png') || f.endsWith('.jpg')))
      .map(async f => {
        const filePath = path.join(outputDir, f);
        const stat = await fs.promises.stat(filePath);
        return { name: f, path: filePath, mtimeMs: stat.mtimeMs };
      }));

    if (screenshots.length <= maxKeep) return;

    const sorted = screenshots.sort((a, b) => b.mtimeMs - a.mtimeMs);

    const toDelete = sorted.slice(maxKeep);
    for (const file of toDelete) {
      await fs.promises.unlink(file.path).catch(() => {});
    }

    console.log(`[清理] 已删除 ${toDelete.length} 个旧截图文件`);
  } catch (err) {
    console.warn(`[警告] 清理旧截图失败: ${err.message}`);
  }
}

async function main() {
  const accounts = parseAccounts(config.ACCOUNTS_FILE);
  if (accounts.length === 0) {
    console.error('[错误] 未加载到有效账号，启动已停止');
    process.exitCode = 1;
    return;
  }

  const progress = loadProgress(config.PROGRESS_FILE);
  const now = new Date().toISOString().replace(/[:.]/g, '-').substring(0, 19);
  const logDir = path.join(config.OUTPUT_DIR, 'logs');
  if (!fs.existsSync(logDir)) {
    fs.mkdirSync(logDir, { recursive: true });
  }
  const LOG_FILE = path.join(logDir, `run-${now}.log`);

  const completedEmails = new Set(progress.completed);
  const failedEmails = new Set(progress.failed);
  const pendingCount = accounts.filter(account => (
    !completedEmails.has(account.email) && !failedEmails.has(account.email)
  )).length;

  console.log('═'.repeat(60));
  console.log('  Google 2FA 自动修改工具');
  console.log('═'.repeat(60));
  console.log(`  总账号数: ${accounts.length}`);
  console.log(`  已完成: ${progress.completed.length}`);
  console.log(`  失败: ${progress.failed.length}`);
  console.log(`  待处理: ${pendingCount}`);
  console.log(`  Headless: ${config.HEADLESS}`);
  console.log(`  SlowMo: ${config.SLOW_MO}ms`);
  console.log(`  日志文件: ${LOG_FILE}`);
  if (config.RECOVERY_EMAIL_POOL.length > 0) {
    console.log(`  恢复邮箱池数量: ${config.RECOVERY_EMAIL_POOL.length}`);
    console.log(`  每N个账号共享一个恢复邮箱: ${config.ACCOUNTS_PER_RECOVERY_EMAIL}`);
  }
  console.log('═'.repeat(60));

  const proxy = process.env.PROXY ? { server: process.env.PROXY } : undefined;

  const userDataDir = config.USER_DATA_DIR;
  if (!fs.existsSync(userDataDir)) {
    fs.mkdirSync(userDataDir, { recursive: true });
  }

  let context = null;
  let page = null;

  try {
    context = await chromium.launchPersistentContext(userDataDir, {
      headless: config.HEADLESS,
      slowMo: config.SLOW_MO,
      channel: process.env.CHROME_CHANNEL || undefined,
      viewport: { width: 1280, height: 900 },
      locale: 'zh-CN',
      timezoneId: 'Asia/Shanghai',
      proxy,
      args: [
        '--disable-blink-features=AutomationControlled',
        '--disable-features=TranslateUI',
        '--lang=zh-CN',
        '--disable-dev-shm-usage',
      ],
    });

    page = await context.newPage();
    await page.setExtraHTTPHeaders({ 'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8' });

    function log(msg) {
      const ts = new Date().toISOString().substring(11, 19);
      const line = `[${ts}] ${redactSensitiveText(msg)}`;
      console.log(line);
      fs.appendFileSync(LOG_FILE, line + '\n');
    }

    let successCount = 0;
    let failCount = 0;
    let skipCount = 0;

    for (let i = 0; i < accounts.length; i++) {
      const email = accounts[i].email;
      if (progress.completed.includes(email)) {
        log(`[跳过] ${i + 1}/${accounts.length} ${maskEmail(email)} (已完成)`);
        skipCount++;
        continue;
      }
      if (progress.failed.includes(email)) {
        log(`[跳过] ${i + 1}/${accounts.length} ${maskEmail(email)} (之前失败)`);
        skipCount++;
        continue;
      }

      log(`[处理] ${i + 1}/${accounts.length} ${maskEmail(email)}`);

      let targetRecoveryEmail = null;
      if (config.RECOVERY_EMAIL_POOL.length > 0) {
        const poolIndex = Math.floor(i / config.ACCOUNTS_PER_RECOVERY_EMAIL) % config.RECOVERY_EMAIL_POOL.length;
        targetRecoveryEmail = config.RECOVERY_EMAIL_POOL[poolIndex];
      }

      const account = accounts[i];
      const accountLog = message => log(redactSensitiveText(message, [
        account.password,
        account.oldSecret,
      ]));
      const result = await loginAndChange2FA(page, account, accountLog, targetRecoveryEmail);

      if (result.success) {
        let recoveryStatus = '';
        if (result.recoveryResult && !result.recoveryResult.success) {
          recoveryStatus = ` | 恢复邮箱: ❌ ${result.recoveryResult.error || ''}`;
        } else if (result.recoveryResult && result.recoveryResult.skipped) {
          recoveryStatus = ' | 恢复邮箱: ⏭ 已设为目标';
        } else if (result.recoveryResult && result.recoveryResult.warning) {
          recoveryStatus = ` | 恢复邮箱: ⚠️ ${result.recoveryResult.warning}`;
        } else if (targetRecoveryEmail) {
          recoveryStatus = ' | 恢复邮箱: ✅ 已修改';
        }

        const effectiveRecoveryEmail = targetRecoveryEmail && result.recoveryResult?.success
          ? targetRecoveryEmail
          : account.recoveryEmail;
        const resultLine = [
          email,
          account.password,
          effectiveRecoveryEmail,
          result.newSecret,
        ].join('----');
        await appendResult(config.RESULT_FILE, resultLine);

        progress.completed.push(email);
        progress.lastIndex = i;
        await saveProgress(config.PROGRESS_FILE, progress);

        if (result.recoveryResult && !result.recoveryResult.success) {
          await appendResult(config.MANUAL_REVIEW_FILE, JSON.stringify({
            email,
            newSecret: result.newSecret,
            reason: result.recoveryResult.error || '恢复邮箱状态未确认',
            stage: 'recovery-email',
            recordedAt: new Date().toISOString(),
          }));
          accountLog('  [复核] 恢复邮箱状态已记录到 output/manual-review.jsonl');
        }

        accountLog(`  ✅ ${email} 操作成功${recoveryStatus}`);
        successCount++;
      } else {
        if (result.requiresManualReview) {
          const reviewRecord = JSON.stringify({
            email,
            newSecret: result.newSecret || null,
            reason: result.error,
            recordedAt: new Date().toISOString(),
          });
          await appendResult(config.MANUAL_REVIEW_FILE, reviewRecord);
          accountLog('  [复核] 已记录到 output/manual-review.jsonl');
        }

        accountLog(`  ❌ ${email} 操作失败: ${result.error}`);
        progress.failed.push(email);
        progress.lastIndex = i;
        await saveProgress(config.PROGRESS_FILE, progress);
        failCount++;
      }

      const hasMoreAccounts = accounts.slice(i + 1).some(nextAccount => (
        !progress.completed.includes(nextAccount.email)
        && !progress.failed.includes(nextAccount.email)
      ));
      if (hasMoreAccounts) {
        log(`  > 等待 ${config.ACCOUNT_DELAY / 1000}s 后处理下一个...`);
        await page.waitForTimeout(config.ACCOUNT_DELAY);
        await context.clearCookies();
        log('  > 已清理浏览器 cookies，准备下一个账号');
      }
    }

    log(`\n══════════════════════════════════════`);
    log(`  执行完毕`);
    log(`  成功: ${successCount} | 失败: ${failCount} | 跳过: ${skipCount}`);
    log(`══════════════════════════════════════`);
  } catch (err) {
    console.error('程序异常:', err);
    console.error('堆栈:', err.stack);
    throw err;
  } finally {
    if (page) {
      try {
        await page.close();
      } catch {}
    }
    if (context) {
      try {
        await context.close();
        console.log('[清理] 浏览器上下文已关闭');
      } catch {}
    }
    await cleanupOldScreenshots();
  }
}

main().catch(err => {
  console.error('程序异常:', err);
  process.exit(1);
});
