/**
 * Playwright Google OAuth 2.0 自动授权模块
 *
 * 驱动无头浏览器自动完成 Google OAuth 2.0 授权流程：
 * 1. 访问授权页，若未登录则自动完成 账户/密码/TOTP 2FA/辅助邮箱 认证
 * 2. 自动绕过“Google 未验证此应用”提示（点击高级 -> 前往不安全）
 * 3. 自动识别并勾选全部所需权限复选框
 * 4. 自动点击继续/允许，拦截并等待回调接口响应成功
 */

import { generateTOTP, getTOTPTimeRemaining } from './totp.mjs';
import path from 'path';
import { OUTPUT_DIR } from './config.mjs';
import { maskEmail, redactSensitiveText } from './redaction.mjs';

function debugScreenshotPath(prefix = 'debug-oauth') {
  const ts = new Date().toISOString().replace(/[:.]/g, '-');
  return path.join(OUTPUT_DIR, `${prefix}-${ts}.png`);
}

async function waitForSafeTOTPWindow(logger) {
  const remaining = getTOTPTimeRemaining();
  if (remaining <= 8) {
    const waitMs = (remaining + 1) * 1000;
    logger(`  [TOTP] 验证码即将过期，等待 ${remaining + 1}s 进入新有效窗口...`);
    await new Promise(resolve => setTimeout(resolve, waitMs));
  }
}

/**
 * 自动填写与提交 TOTP 验证码
 */
async function handleTOTPChallenge(page, secret, logger) {
  logger('  [2FA] 检测到两步验证页面，准备计算 TOTP...');
  for (let attempt = 0; attempt < 3; attempt++) {
    if (attempt > 0) {
      logger(`  [2FA] 第 ${attempt + 1} 次尝试重新生成验证码...`);
    }

    await waitForSafeTOTPWindow(logger);
    const token = generateTOTP(secret);
    if (!token) {
      return { success: false, error: 'TOTP 密钥无法生成验证码' };
    }

    const totpInput = await page.$(
      'input[type="tel"], input[autocomplete="one-time-code"], input[name="idvany_phone"], #idvany_phone, input[id*="totp"]'
    ).catch(() => null);

    if (totpInput) {
      await totpInput.fill('');
      await page.waitForTimeout(200);
      await totpInput.fill(token);
      await page.waitForTimeout(600);
    }

    const submitBtns = [
      '#idvany_primary',
      'button:has-text("下一步")',
      'button:has-text("继续")',
      'button:has-text("Next")',
      'button:has-text("Verify")',
      'button[type="submit"]',
    ];

    for (const sel of submitBtns) {
      const btn = await page.$(sel).catch(() => null);
      if (btn && await btn.isVisible().catch(() => false)) {
        await btn.click().catch(() => {});
        break;
      }
    }

    logger('  [2FA] 已提交验证码，等待验证结果...');
    await page.waitForTimeout(6000);

    const currentUrl = await page.url();
    if (!currentUrl.includes('challenge') && !currentUrl.includes('signin') && !currentUrl.includes('idvany')) {
      logger('  [2FA] ✅ 两步验证通过');
      return { success: true };
    }

    if (await page.isVisible('div:has-text("验证码不正确"), div:has-text("Invalid code")').catch(() => false)) {
      logger('  [2FA] ⚠️ 验证码被拒，等待下一个刷新窗口...');
      await page.waitForTimeout((getTOTPTimeRemaining() + 1) * 1000);
    }
  }

  await page.screenshot({ path: debugScreenshotPath('oauth-totp-fail'), fullPage: true }).catch(() => {});
  return { success: false, error: 'TOTP 验证码多次尝试均被拒绝' };
}

/**
 * 处理辅助邮箱确认提示
 */
async function handleRecoveryEmailChallenge(page, recoveryEmail, logger) {
  if (!recoveryEmail) return { success: true };

  const recoverySelectors = [
    'input[name="knowledgePreregisteredEmailResponse"]',
    'input[aria-label*="辅助邮箱"]',
    'input[aria-label*="recovery email" i]',
    'input[placeholder*="辅助邮箱"]',
    'input[placeholder*="recovery email" i]',
  ];

  for (const sel of recoverySelectors) {
    const input = await page.$(sel).catch(() => null);
    if (input && await input.isVisible().catch(() => false)) {
      logger(`  [安全验证] 检测到辅助邮箱确认要求，正在填入: ${maskEmail(recoveryEmail)}`);
      await input.fill(recoveryEmail);
      await page.waitForTimeout(500);

      const nextBtn = await page.$(
        '#knowledge-preregistered-email-responseNext, button:has-text("下一步"), button:has-text("Next")'
      ).catch(() => null);

      if (nextBtn) {
        await nextBtn.click();
      } else {
        await page.keyboard.press('Enter');
      }
      await page.waitForTimeout(4000);
      return { success: true };
    }
  }

  // 检查是否有关联辅助邮箱的选项入口
  const recoveryChoice = await page.$(
    'div[data-challengetype="12"], div:has-text("确认您的辅助邮箱"), div:has-text("Confirm your recovery email")'
  ).catch(() => null);

  if (recoveryChoice && await recoveryChoice.isVisible().catch(() => false)) {
    logger('  [安全验证] 点击确认辅助邮箱入口...');
    await recoveryChoice.click().catch(() => {});
    await page.waitForTimeout(2500);
    return handleRecoveryEmailChallenge(page, recoveryEmail, logger);
  }

  return { success: true };
}

/**
 * 绕过“此应用未经过验证 / Google hasn't verified this app”警告
 */
async function handleUnverifiedAppWarning(page, logger) {
  const warningTexts = [
    'Google 未验证此应用',
    '此应用未经 Google 验证',
    '此应用未经过验证',
    "Google hasn't verified this app",
    "This app isn't verified",
    'Advanced',
    '高级',
  ];

  let hasWarning = false;
  for (const text of warningTexts) {
    if (await page.isVisible(`text="${text}"`).catch(() => false)) {
      hasWarning = true;
      break;
    }
  }

  const advancedBtn = await page.$(
    '#advancedButton, button:has-text("高级"), a:has-text("高级"), button:has-text("Advanced"), a:has-text("Advanced")'
  ).catch(() => null);

  if (hasWarning || advancedBtn) {
    logger('  [授权安全] 检测到未验证应用告警提示，正在跳过...');
    if (advancedBtn) {
      await advancedBtn.click().catch(() => {});
      await page.waitForTimeout(1500);
    }

    const proceedLinks = [
      '#link-proceed',
      'a:has-text("转到")',
      'a:has-text("不安全")',
      'a:has-text("unsafe" i)',
      'a:has-text("Go to" i)',
      'a[href*="proceed"]',
    ];

    for (const sel of proceedLinks) {
      const link = await page.$(sel).catch(() => null);
      if (link && await link.isVisible().catch(() => false)) {
        logger('  [授权安全] 点击继续前往目标应用（不安全）...');
        await link.click().catch(() => {});
        await page.waitForTimeout(2500);
        return true;
      }
    }
  }

  return false;
}

/**
 * 勾选全部权限复选框并点击允许/继续
 */
async function handleScopeConsentAndSubmit(page, logger) {
  logger('  [权限确认] 正在检查 OAuth 权限勾选与同意按钮...');
  await page.waitForTimeout(1500);

  // 1. 勾选所有未选中的权限复选框
  const uncheckedSelectors = [
    'div[role="checkbox"][aria-checked="false"]',
    'input[type="checkbox"]:not(:checked)',
  ];

  for (const sel of uncheckedSelectors) {
    const checkboxes = await page.$$(sel).catch(() => []);
    for (const cb of checkboxes) {
      if (await cb.isVisible().catch(() => false)) {
        await cb.click().catch(() => {});
        await page.waitForTimeout(300);
      }
    }
    if (checkboxes.length > 0) {
      logger(`  [权限确认] 已勾选 ${checkboxes.length} 个权限项`);
    }
  }

  // 2. 点击“全选”按钮如果存在
  const selectAll = await page.$(
    'button:has-text("全选"), button:has-text("Select all"), div[role="checkbox"]:has-text("全选")'
  ).catch(() => null);
  if (selectAll && await selectAll.isVisible().catch(() => false)) {
    const isChecked = await selectAll.getAttribute('aria-checked').catch(() => '');
    if (isChecked !== 'true') {
      await selectAll.click().catch(() => {});
      await page.waitForTimeout(500);
    }
  }

  // 3. 点击“继续 / 允许 / 同意”
  const allowButtons = [
    '#submit_approve_access',
    'button:has-text("继续")',
    'button:has-text("允许")',
    'button:has-text("Continue")',
    'button:has-text("Allow")',
    'button[name="submit"]',
  ];

  for (const sel of allowButtons) {
    const btn = await page.$(sel).catch(() => null);
    if (btn && await btn.isVisible().catch(() => false)) {
      logger('  [权限确认] 点击同意并继续授权...');
      await btn.click().catch(() => {});
      await page.waitForTimeout(2000);
      return true;
    }
  }

  return false;
}

/**
 * 执行完整的 Google OAuth 自动化授权流程
 *
 * @param {import('playwright').Page} page Playwright 页面实例
 * @param {Object} account 账号凭证 { email, password, secret, recovery }
 * @param {string} authUrl OAuth 授权链接
 * @param {Function} logger 日志函数
 * @param {Object} options 选项配置
 */
export async function authorizeGoogleOAuth(page, account, authUrl, logger = console.log, options = {}) {
  const { email, password, secret, recovery } = account;
  const timeoutMs = options.timeoutMs || 90000;
  const startTime = Date.now();

  const writeLog = logger;
  const safeLogger = (msg) => writeLog(redactSensitiveText(msg, [password, secret]));

  safeLogger(`[OAuth 授权] 开始自动授权账号: ${maskEmail(email)}`);

  try {
    // 注入防检测特性
    await page.addInitScript(() => {
      Object.defineProperty(navigator, 'webdriver', { get: () => false });
      Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
      Object.defineProperty(navigator, 'languages', { get: () => ['zh-CN', 'zh', 'en'] });
    });

    safeLogger(`  [导航] 访问授权链接: ${authUrl.substring(0, 80)}...`);
    await page.goto(authUrl, {
      waitUntil: 'domcontentloaded',
      timeout: 45000,
    });
    await page.waitForTimeout(2000);

    // 持续监听和驱动页面状态，直至进入回调或者超时
    let isAuthorized = false;
    let authError = null;

    while (Date.now() - startTime < timeoutMs) {
      const currentUrl = await page.url();

      // 1. 检查是否已经重定向到本系统回调接口
      if (currentUrl.includes('/api/gmail/oauth/callback')) {
        safeLogger('  [回调] 已到达授权回调接口，校验响应状态...');
        await page.waitForLoadState('networkidle').catch(() => {});
        const bodyContent = await page.innerText('body').catch(() => '');

        if (
          bodyContent.includes('Gmail 授权成功') ||
          bodyContent.includes('"success":true') ||
          bodyContent.includes('"success": true') ||
          bodyContent.includes('授权成功')
        ) {
          safeLogger(`  ✅ [完成] 账号 ${email} 授权成功并完成凭证保存`);
          isAuthorized = true;
          break;
        } else if (bodyContent.includes('error') || bodyContent.includes('失败')) {
          authError = `回调接口返回错误: ${bodyContent.substring(0, 200)}`;
          break;
        }
      }

      // 2. 检查是否在账号选择界面 (Choose an account)
      const accountPicker = await page.$(
        `div[data-identifier="${email}"], div:has-text("${email}")[role="link"]`
      ).catch(() => null);
      if (accountPicker && await accountPicker.isVisible().catch(() => false)) {
        safeLogger('  [登录] 检测到已存在的目标账号选项，直接点击选择...');
        await accountPicker.click().catch(() => {});
        await page.waitForTimeout(3000);
        continue;
      }

      // 3. 检查邮箱输入框
      const emailInput = await page.$(
        '#identifierId, input[type="email"], input[name="identifier"]'
      ).catch(() => null);
      if (emailInput && await emailInput.isVisible().catch(() => false)) {
        safeLogger('  [登录] 输入邮箱账号...');
        await emailInput.fill(email);
        await page.waitForTimeout(500);
        const nextBtn = await page.$(
          '#identifierNext, button:has-text("下一步"), button:has-text("Next")'
        ).catch(() => null);
        if (nextBtn) {
          await nextBtn.click();
        } else {
          await page.keyboard.press('Enter');
        }
        await page.waitForTimeout(3500);
        continue;
      }

      // 4. 检查密码输入框
      const passInput = await page.$(
        'input[type="password"], input[name="Passwd"], #password input'
      ).catch(() => null);
      if (passInput && await passInput.isVisible().catch(() => false)) {
        safeLogger('  [登录] 输入密码...');
        await passInput.fill(password);
        await page.waitForTimeout(500);
        const passNext = await page.$(
          '#passwordNext, button:has-text("下一步"), button:has-text("Next")'
        ).catch(() => null);
        if (passNext) {
          await passNext.click();
        } else {
          await page.keyboard.press('Enter');
        }
        await page.waitForTimeout(4000);
        continue;
      }

      // 5. 检查两步验证挑战
      const totpSelector = 'input[type="tel"], input[autocomplete="one-time-code"], #idvany_phone';
      if (await page.isVisible(totpSelector).catch(() => false)) {
        if (!secret) {
          authError = '账号需要两步验证，但未提供 2FA TOTP 密钥';
          break;
        }
        const totpRes = await handleTOTPChallenge(page, secret, safeLogger);
        if (!totpRes.success) {
          authError = totpRes.error;
          break;
        }
        continue;
      }

      // 6. 检查辅助邮箱确认
      await handleRecoveryEmailChallenge(page, recovery, safeLogger);

      // 7. 检查未验证应用警告（高级 -> 前往不安全）
      const handledWarning = await handleUnverifiedAppWarning(page, safeLogger);
      if (handledWarning) {
        continue;
      }

      // 8. 检查权限勾选与同意提交
      const handledConsent = await handleScopeConsentAndSubmit(page, safeLogger);
      if (handledConsent) {
        // 等待重定向
        await page.waitForTimeout(3000);
        continue;
      }

      // 9. 检查是否有已知错误提示
      const errorText = await page.innerText('.dEOOab, .o6cuMc, [aria-live="assertive"]').catch(() => '');
      if (errorText) {
        if (errorText.includes('密码不正确') || errorText.includes('Wrong password')) {
          authError = '账号密码不正确';
          break;
        }
      }

      await page.waitForTimeout(1500);
    }

    if (isAuthorized) {
      return { success: true, email };
    }

    const failureReason = authError || 'OAuth 自动授权超时（未能到达成功回调页面）';
    safeLogger(`  ❌ [失败] ${failureReason}`);
    const screenshot = debugScreenshotPath('oauth-error');
    await page.screenshot({ path: screenshot, fullPage: true }).catch(() => {});

    return {
      success: false,
      error: failureReason,
      screenshot: path.basename(screenshot),
    };
  } catch (err) {
    const errorMsg = `OAuth 自动化异常: ${err.message}`;
    safeLogger(`  [异常] ${errorMsg}`);
    const screenshot = debugScreenshotPath('oauth-fatal');
    await page.screenshot({ path: screenshot, fullPage: true }).catch(() => {});
    return {
      success: false,
      error: errorMsg,
      screenshot: path.basename(screenshot),
    };
  }
}
