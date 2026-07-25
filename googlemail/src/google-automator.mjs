import { generateBase32Secret, generateTOTP, getTOTPTimeRemaining } from './totp.mjs';
import { maskEmail, redactSensitiveText } from './redaction.mjs';

function generateNewSecret() {
  return generateBase32Secret(20);
}

function manualReviewResult(error, newSecret) {
  return {
    success: false,
    error,
    newSecret,
    requiresManualReview: true,
  };
}

export async function setupNewAuthenticator(page, logger, createSecret = generateNewSecret) {
  const newSecret = createSecret();
  logger('  已生成新密钥');

  const cantScanSelectors = [
    'a:has-text("无法扫描")',
    'a:has-text("Can\'t scan")',
    'button:has-text("无法扫描")',
    'button:has-text("Can\'t scan")',
    'a:has-text("手动输入")',
    'button:has-text("手动输入")',
  ];

  for (const sel of cantScanSelectors) {
    if (await page.isVisible(sel).catch(() => false)) {
      await page.click(sel);
      logger('  点击"无法扫描"以手动输入密钥...');
      await page.waitForTimeout(2000);
      break;
    }
  }

  const secretInputSelectors = [
    'input[placeholder*="密钥"]',
    'input[placeholder*="secret"]',
    'input[name="secret"]',
    'input[placeholder*="key"]',
    'input[type="text"]:not([name="email"])',
  ];

  let secretInput = null;
  for (const sel of secretInputSelectors) {
    secretInput = await page.$(sel).catch(() => null);
    if (secretInput) break;
  }

  if (!secretInput) {
    logger('  [错误] 找不到手动输入密钥的输入框，需要人工复核账号状态');
    await page.screenshot({ path: 'output/debug-setup-secret-input.png' }).catch(() => {});
    return manualReviewResult('找不到手动输入密钥的输入框', newSecret);
  }

  await secretInput.fill(newSecret);
  logger('  已填入新密钥');
  await page.waitForTimeout(1000);

  const newToken = generateTOTP(newSecret);
  if (!newToken) {
    return manualReviewResult('新密钥未生成有效验证码', newSecret);
  }
  logger('  已生成新验证码');

  const codeInputSelectors = [
    'input[placeholder*="验证码"]',
    'input[placeholder*="code"]',
    'input[name="code"]',
    'input[type="tel"]:last-of-type',
  ];

  let codeInput = null;
  for (const sel of codeInputSelectors) {
    codeInput = await page.$(sel).catch(() => null);
    if (codeInput) break;
  }

  if (!codeInput) {
    logger('  [错误] 找不到新验证器验证码输入框，需要人工复核账号状态');
    await page.screenshot({ path: 'output/debug-setup-code-input.png' }).catch(() => {});
    return manualReviewResult('找不到新验证器验证码输入框', newSecret);
  }

  await codeInput.fill(newToken);
  logger('  已填入验证码');
  await page.waitForTimeout(1000);

  const verifySelectors = [
    'button:has-text("验证")',
    'button:has-text("确认")',
    'button:has-text("Verify")',
    'button:has-text("Confirm")',
    'button:has-text("下一步")',
  ];

  let verifyClicked = false;
  for (const sel of verifySelectors) {
    if (await page.isVisible(sel).catch(() => false)) {
      await page.click(sel);
      verifyClicked = true;
      await page.waitForTimeout(3000);
      break;
    }
  }

  if (!verifyClicked) {
    logger('  [错误] 找不到验证提交按钮，需要人工复核账号状态');
    return manualReviewResult('找不到验证提交按钮', newSecret);
  }

  const verificationErrorSelectors = [
    'div:has-text("验证码不正确")',
    'div:has-text("验证码错误")',
    'div:has-text("Invalid code")',
    'div:has-text("Incorrect code")',
  ];
  for (const sel of verificationErrorSelectors) {
    if (await page.isVisible(sel).catch(() => false)) {
      logger('  [错误] 新验证器验证码被拒绝，需要人工复核账号状态');
      return manualReviewResult('新验证器验证码被拒绝', newSecret);
    }
  }

  if (await codeInput.isVisible().catch(() => false)) {
    logger('  [错误] 提交后仍停留在验证码输入步骤，需要人工复核账号状态');
    return manualReviewResult('新验证器设置未得到页面确认', newSecret);
  }

  const saveSelectors = [
    'button:has-text("完成")',
    'button:has-text("保存")',
    'button:has-text("Done")',
    'button:has-text("Save")',
  ];
  for (const sel of saveSelectors) {
    if (await page.isVisible(sel).catch(() => false)) {
      await page.click(sel);
      await page.waitForTimeout(3000);
      break;
    }
  }

  return { success: true, newSecret };
}

async function waitForSafeTOTPWindow(page, logger) {
  const remaining = getTOTPTimeRemaining();
  if (remaining <= 8) {
    const waitMs = (remaining + 1) * 1000;
    logger(`  TOTP 即将过期，等待 ${remaining + 1}s 后重新生成...`);
    await page.waitForTimeout(waitMs);
  }
}

async function handlePasswordReverify(page, password, logger) {
  const passwordSelectors = [
    'input[type="password"]',
    'input[name="Passwd"]',
    'input[name="password"]',
  ];

  let pwInput = null;
  for (const sel of passwordSelectors) {
    pwInput = await page.$(sel).catch(() => null);
    if (pwInput) break;
  }

  if (!pwInput) return false;

  logger('    需要重新验证密码...');
  await pwInput.fill(password);
  await page.waitForTimeout(500 + Math.random() * 300);

  const nextSelectors = [
    'button[type="submit"]',
    'button:has-text("下一步")',
    'button:has-text("Next")',
    'button:has-text("确认")',
    '#passwordNext',
  ];
  for (const sel of nextSelectors) {
    const btn = await page.$(sel).catch(() => null);
    if (btn) {
      await btn.click();
      break;
    }
  }
  await page.waitForTimeout(3000 + Math.random() * 1000);
  return true;
}

async function changeRecoveryEmail(page, targetEmail, password, logger) {
  logger(`  目标恢复邮箱: ${targetEmail}`);

  try {
    await page.goto('https://myaccount.google.com/recovery/email', {
      waitUntil: 'domcontentloaded',
      timeout: 30000,
    });
    await page.waitForTimeout(3000 + Math.random() * 1000);

    await handlePasswordReverify(page, password, logger);

    const currentUrl = await page.url();
    if (currentUrl.includes('signin') || currentUrl.includes('challenge')) {
      logger('  [错误] 访问恢复邮箱页面需要重新登录');
      await page.screenshot({ path: 'output/debug-recovery-reauth.png', fullPage: true }).catch(() => {});
      return { success: false, error: '访问恢复邮箱页面需要重新登录' };
    }

    const currentEmailEl = await page.$('input[value][type="email"], div[data-email]').catch(() => null);
    if (currentEmailEl) {
      const currentValue = await currentEmailEl.getAttribute('value').catch(() => '');
      if (String(currentValue || '').toLowerCase() === targetEmail.toLowerCase()) {
        logger('  ✅ 恢复邮箱已为目标邮箱，无需修改');
        return { success: true, skipped: true };
      }
    }

    const pageText = await page.innerText('body').catch(() => '');
    if (pageText.includes(targetEmail)) {
      logger('  ✅ 恢复邮箱已为目标邮箱，无需修改');
      return { success: true, skipped: true };
    }

    logger('  查找恢复邮箱编辑入口...');

    const editSelectors = [
      'a[aria-label*="恢复邮箱"]',
      'a[aria-label*="Recovery email"]',
      'span:has-text("恢复邮箱")',
      'span:has-text("Recovery email")',
      'div:has-text("恢复邮箱")',
      'div:has-text("Recovery email")',
      'a[href*="recovery"]',
      'button[aria-label*="编辑"]',
      'button[aria-label*="Edit"]',
      'span:has-text("编辑")',
      'svg[aria-label*="编辑"]',
    ];

    let editClicked = false;
    for (const sel of editSelectors) {
      const el = await page.$(sel).catch(() => null);
      if (el) {
        await el.click().catch(() => {});
        await page.waitForTimeout(1500);
        editClicked = true;
        logger(`    点击编辑入口: ${sel}`);
        break;
      }
    }

    if (!editClicked) {
      const allClickable = await page.$$('a, button, span[role="button"], div[role="button"]');
      for (const el of allClickable) {
        const text = await el.innerText().catch(() => '');
        if (text.includes('编辑') || text.includes('Edit') || text.includes('恢复邮箱') || text.includes('Recovery')) {
          await el.click().catch(() => {});
          await page.waitForTimeout(1500);
          editClicked = true;
          logger(`    点击编辑元素: "${text.substring(0, 30)}"`);
          break;
        }
      }
    }

    if (!editClicked) {
      const addSelectors = [
        'button:has-text("添加恢复邮箱")',
        'a:has-text("添加恢复邮箱")',
        'button:has-text("Add recovery email")',
        'a:has-text("Add recovery email")',
        'span:has-text("添加")',
      ];
      for (const sel of addSelectors) {
        const el = await page.$(sel).catch(() => null);
        if (el) {
          await el.click().catch(() => {});
          await page.waitForTimeout(1500);
          editClicked = true;
          logger(`    点击添加按钮: ${sel}`);
          break;
        }
      }
    }

    await page.waitForTimeout(1000);

    const emailInputSelectors = [
      'input[type="email"]',
      'input[name="email"]',
      'input[aria-label*="邮箱"]',
      'input[aria-label*="email"]',
      'input[placeholder*="邮箱"]',
      'input[placeholder*="email"]',
    ];

    let emailInput = null;
    for (const sel of emailInputSelectors) {
      emailInput = await page.$(sel).catch(() => null);
      if (emailInput) break;
    }

    if (!emailInput) {
      logger('  [警告] 找不到恢复邮箱输入框，正在截图...');
      await page.screenshot({ path: 'output/debug-recovery-no-input.png', fullPage: true }).catch(() => {});
      return { success: false, error: '找不到恢复邮箱输入框' };
    }

    const existingValue = await emailInput.getAttribute('value').catch(() => '');
    if (String(existingValue || '').toLowerCase() === targetEmail.toLowerCase()) {
      logger('  ✅ 输入框已为目标邮箱，无需修改');
      return { success: true, skipped: true };
    }

    await emailInput.click({ clickCount: 3 });
    await page.waitForTimeout(300);
    await emailInput.fill(targetEmail);
    await page.waitForTimeout(500 + Math.random() * 300);
    logger(`  已填入新恢复邮箱: ${targetEmail}`);

    const saveSelectors = [
      'button:has-text("保存")',
      'button:has-text("Save")',
      'button:has-text("下一步")',
      'button:has-text("Next")',
      'button:has-text("确认")',
      'button:has-text("Done")',
      'button[type="submit"]',
    ];

    let saveClicked = false;
    for (const sel of saveSelectors) {
      const btn = await page.$(sel).catch(() => null);
      if (btn) {
        await btn.click();
        saveClicked = true;
        logger('  已点击保存按钮');
        break;
      }
    }

    if (!saveClicked) {
      await page.keyboard.press('Enter');
      logger('  已按回车提交');
    }

    await page.waitForTimeout(4000 + Math.random() * 2000);

    const verifySelectors = [
      'input[aria-label*="验证码"]',
      'input[placeholder*="验证码"]',
      'input[placeholder*="code"]',
      'input[aria-label*="code"]',
      'input[type="tel"]',
      'div:has-text("验证码")',
      'div:has-text("verification code")',
      'span:has-text("验证您的")',
    ];

    let needsVerification = false;
    for (const sel of verifySelectors) {
      if (await page.$(sel).catch(() => null)) {
        needsVerification = true;
        break;
      }
    }

    if (needsVerification) {
      logger('  ⚠️ Google 要求验证新恢复邮箱，等待手动输入验证码...');
      await page.screenshot({ path: 'output/debug-recovery-verify.png', fullPage: true }).catch(() => {});

      const verifyInput = await page.$('input[type="tel"], input[aria-label*="验证码"], input[aria-label*="code"]').catch(() => null);
      if (verifyInput) {
        logger('  等待60秒以手动输入验证码...');

        let verified = false;
        const startTime = Date.now();
        while (Date.now() - startTime < 60000) {
          await page.waitForTimeout(3000);

          if (!verifyInput) break;

          const currentVal = await verifyInput.getAttribute('value').catch(() => '');
          if (currentVal && currentVal.length >= 6) {
            logger('  检测到验证码输入');

            const confirmBtn = await page.$('button:has-text("验证"), button:has-text("Verify"), button:has-text("下一步"), button:has-text("Next")').catch(() => null);
            if (confirmBtn) {
              await confirmBtn.click();
              await page.waitForTimeout(3000);
            }

            const url = await page.url();
            if (!url.includes('verify') && !url.includes('code')) {
              verified = true;
              logger('  ✅ 恢复邮箱验证通过');
            }
            break;
          }
        }

        if (!verified) {
          logger('  [错误] 未确认恢复邮箱验证码，修改状态待复核');
          return { success: false, error: '恢复邮箱已提交但未完成验证' };
        }
      }
    }

    await page.waitForTimeout(2000);

    const finalText = await page.innerText('body').catch(() => '');
    if (finalText.includes(targetEmail)) {
      logger('  ✅ 恢复邮箱修改成功');
      return { success: true };
    }

    logger('  [错误] 页面未确认目标恢复邮箱，修改状态待复核');
    return { success: false, error: '提交后未确认目标恢复邮箱' };
  } catch (err) {
    const errorMsg = `恢复邮箱修改失败: ${err.message}`;
    logger(`  [异常] ${errorMsg}`);
    logger(`  [堆栈] ${err.stack?.substring(0, 500) || '无堆栈信息'}`);
    await page.screenshot({ path: 'output/debug-recovery-error.png', fullPage: true }).catch(() => {});
    return { success: false, error: errorMsg };
  }
}

export async function loginAndChange2FA(page, account, logger, targetRecoveryEmail = null) {
  const { email, password, oldSecret } = account;
  const writeLog = logger;
  logger = message => writeLog(redactSensitiveText(message, [password, oldSecret]));
  logger(`[开始] 账号 ${maskEmail(email)}`);

  try {
    await page.addInitScript(() => {
      Object.defineProperty(navigator, 'webdriver', { get: () => false });
      Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
      Object.defineProperty(navigator, 'languages', { get: () => ['zh-CN', 'zh', 'en'] });
      const originalQuery = window.navigator.permissions.query;
      window.navigator.permissions.query = (parameters) => (
        parameters.name === 'notifications'
          ? Promise.resolve({ state: 'granted' })
          : originalQuery.call(window.navigator.permissions, parameters)
      );
    });

    logger('  正在打开 Google 登录页面...');
    await page.goto('https://accounts.google.com/signin', {
      waitUntil: 'domcontentloaded',
      timeout: 30000,
    });
    await page.waitForTimeout(3000 + Math.random() * 2000);

    const emailInputSelectors = [
      'input[type="email"]',
      'input[name="identifier"]',
      '#identifierId',
    ];

    let emailInput = null;
    for (const sel of emailInputSelectors) {
      emailInput = await page.$(sel).catch(() => null);
      if (emailInput) break;
    }

    if (!emailInput) {
      logger('  [错误] 找不到邮箱输入框');
      logger(`  [调试] 页面标题: ${await page.title()}`);
      logger(`  [调试] 页面URL: ${await page.url()}`);
      return { success: false, error: '找不到邮箱输入框' };
    }

    await emailInput.fill(email);
    await page.waitForTimeout(1000 + Math.random() * 500);

    const nextBtnSelectors = ['#identifierNext', 'button:has-text("下一步")', 'button:has-text("Next")'];
    let nextBtn = null;
    for (const sel of nextBtnSelectors) {
      nextBtn = await page.$(sel).catch(() => null);
      if (nextBtn) break;
    }
    if (nextBtn) {
      await nextBtn.click();
    } else {
      await page.keyboard.press('Enter');
    }

    logger('  已输入邮箱，等待密码输入框...');

    try {
      await page.waitForSelector('input[type="password"]', { timeout: 20000 });
    } catch {
      const passInput = await page.$('input[name="Passwd"]').catch(() => null);
      if (passInput) {
        logger('  找到备用密码框...');
      } else {
        logger('  [警告] 未找到密码框，可能页面结构变化');
        logger(`  [调试] URL: ${await page.url()}`);
        return { success: false, error: '找不到密码框' };
      }
    }

    await page.waitForTimeout(1500 + Math.random() * 1000);

    const passwordSelectors = [
      'input[type="password"]',
      'input[name="Passwd"]',
      '#password input',
    ];

    let passwordInput = null;
    for (const sel of passwordSelectors) {
      passwordInput = await page.$(sel).catch(() => null);
      if (passwordInput) break;
    }

    if (!passwordInput) {
      return { success: false, error: '找不到密码框' };
    }

    await passwordInput.fill(password);
    await page.waitForTimeout(1000 + Math.random() * 500);

    const passNextSelectors = ['#passwordNext', 'button:has-text("下一步")', 'button:has-text("Next")'];
    let passNext = null;
    for (const sel of passNextSelectors) {
      passNext = await page.$(sel).catch(() => null);
      if (passNext) break;
    }
    if (passNext) {
      await passNext.click();
    } else {
      await page.keyboard.press('Enter');
    }

    logger('  已输入密码，等待两步验证...');
    await page.waitForTimeout(5000 + Math.random() * 2000);

    const totpSelectors = [
      'input[type="tel"]',
      'input[autocomplete="one-time-code"]',
      'input[name="idvany_phone"]',
      'input[id="idvany_phone"]',
    ];

    let totpInput = null;
    for (const sel of totpSelectors) {
      totpInput = await page.$(sel).catch(() => null);
      if (totpInput) break;
    }

    if (totpInput) {
      logger('  进入两步验证，准备生成 TOTP...');

      let totpSuccess = false;

      for (let attempt = 0; attempt < 3 && !totpSuccess; attempt++) {
        if (attempt > 0) {
          logger(`  重试第 ${attempt + 1} 次，重新生成当前窗口验证码`);
        }

        await waitForSafeTOTPWindow(page, logger);
        const token = generateTOTP(oldSecret);
        if (!token) {
          return { success: false, error: 'TOTP密钥无法生成验证码' };
        }

        const inputEl = await page.$('input[type="tel"], input[autocomplete="one-time-code"]').catch(() => null);
        if (inputEl) {
          await inputEl.fill('');
          await page.waitForTimeout(300);
          await inputEl.fill(token);
          await page.waitForTimeout(1000);
        }

        const submitSelectors = [
          '#idvany_primary',
          'button:has-text("下一步")',
          'button:has-text("继续")',
          'button:has-text("Next")',
          'button:has-text("Verify")',
        ];
        for (const sel of submitSelectors) {
          const btn = await page.$(sel).catch(() => null);
          if (btn) {
            await btn.click();
            break;
          }
        }
        logger('  已提交当前窗口验证码，等待处理...');
        await page.waitForTimeout(8000 + Math.random() * 2000);

        const url = await page.url();
        if (!url.includes('challenge') && !url.includes('signin') && !url.includes('verify')) {
          totpSuccess = true;
          logger('  ✅ 两步验证通过');
        } else if (await page.isVisible('div:has-text("验证码不正确")').catch(() => false)) {
          logger('  ⚠️ 当前窗口验证码不正确');
        } else if (await page.isVisible('div:has-text("请重试")').catch(() => false)) {
          logger('  ⚠️ 验证码被拒绝，等待下一个窗口...');
          await page.waitForTimeout((getTOTPTimeRemaining() + 1) * 1000);
        } else {
          logger('  ⚠️ 仍在验证页面，等待下一个窗口...');
          await page.waitForTimeout((getTOTPTimeRemaining() + 1) * 1000);
        }
      }

      if (!totpSuccess) {
        logger('  所有验证码均被拒绝，正在截图...');
        await page.screenshot({ path: 'output/debug-totp-fail.png', fullPage: true }).catch(() => {});
        return { success: false, error: '所有TOTP验证码均被拒绝，密钥可能不正确' };
      }
    } else {
      logger('  未检测到两步验证输入框，正在截图分析...');
      await page.screenshot({ path: 'output/debug-no-totp.png', fullPage: true }).catch(() => {});
      logger('  截图已保存到 output/debug-no-totp.png');

      const altMethods = [
        'div:has-text("尝试其他方式")',
        'button:has-text("试试其他方法")',
        'div:has-text("无法验证")',
        'span:has-text("其他验证方式")',
      ];
      for (const sel of altMethods) {
        if (await page.$(sel).catch(() => null)) {
          logger(`  检测到备选验证选项: ${sel}`);
        }
      }
    }

    const currentUrl = await page.url();
    if (currentUrl.includes('challenge') || currentUrl.includes('signin') || currentUrl.includes('verify')) {
      logger(`  [错误] 仍在验证页面: ${currentUrl}`);
      if (currentUrl.includes('challenge/pwd')) {
        logger('  检测到密码再验证页面，可能密码错误或需要额外验证');
        await page.screenshot({ path: 'output/debug-challenge-pwd.png', fullPage: true }).catch(() => {});
      }
      if (await page.isVisible('div:has-text("验证码错误")')) {
        return { success: false, error: '验证码错误' };
      }
      if (await page.isVisible('div:has-text("两步验证")')) {
        return { success: false, error: '需要额外验证步骤' };
      }
      return { success: false, error: `验证未通过: ${currentUrl}` };
    }

    logger('  ✅ 登录成功，导航到安全设置...');
    await page.goto('https://myaccount.google.com/security', {
      waitUntil: 'domcontentloaded',
      timeout: 45000,
    });
    await page.waitForTimeout(4000 + Math.random() * 2000);

    if (await page.url().includes('signin')) {
      logger('  [错误] 导航时需要重新登录');
      return { success: false, error: '导航时需要重新登录' };
    }

    logger('  查找两步验证入口...');

    const twoStepSelectors = [
      'a[href*="signinoptions"]',
      'a[href*="two-step"]',
      'a[href*="twostep"]',
      'a[href*="2sv"]',
      'div:has-text("两步验证")',
      'div:has-text("2-Step Verification")',
    ];

    let twoStepLink = null;
    for (const sel of twoStepSelectors) {
      twoStepLink = await page.$(sel).catch(() => null);
      if (twoStepLink) {
        await twoStepLink.click();
        break;
      }
    }

    if (!twoStepLink) {
      logger('  [备用方案] 直接导航到两步验证页面...');
      await page.goto('https://myaccount.google.com/signinoptions/two-step-verification', {
        waitUntil: 'domcontentloaded',
        timeout: 45000,
      });
      await page.waitForTimeout(4000);
    }

    await page.waitForTimeout(3000 + Math.random() * 2000);

    if (await page.url().includes('signin') || await page.url().includes('challenge')) {
      logger('  [错误] 需要重新登录确认');
      if (await page.isVisible('input[type="password"]')) {
        await page.fill('input[type="password"]', password);
        const confirmBtn = await page.$('button[type="submit"]');
        if (confirmBtn) await confirmBtn.click();
        await page.waitForTimeout(4000);
      }
    }

    logger('  查找关闭两步验证按钮...');

    const turnOffSelectors = [
      'button:has-text("关闭")',
      'a:has-text("关闭")',
      'div[role="button"]:has-text("关闭")',
      'button:has-text("Turn off")',
      'a:has-text("Turn off")',
      '.Xiq6ie',
      '[jsname="U818Qd"]',
      'button:has-text("关")',
    ];

    let foundOff = false;
    for (const sel of turnOffSelectors) {
      if (await page.isVisible(sel).catch(() => false)) {
        await page.click(sel);
        foundOff = true;
        logger('  ✅ 已点击关闭按钮');
        break;
      }
    }

    if (!foundOff) {
      const allButtons = await page.$$('button, a, div[role="button"]');
      for (const btn of allButtons) {
        const text = await btn.innerText().catch(() => '');
        if (text.includes('关闭') || text.includes('Turn off')) {
          await btn.click();
          foundOff = true;
          break;
        }
      }
    }

    if (!foundOff) {
      logger('  [警告] 找不到关闭按钮，尝试截图...');
      await page.screenshot({ path: 'output/debug-turnoff.png' }).catch(() => {});
      return { success: false, error: '找不到关闭两步验证的按钮' };
    }

    await page.waitForTimeout(3000);

    const confirmOffSelectors = [
      'button:has-text("关闭")',
      'button:has-text("确认")',
      'button:has-text("Turn off")',
      'button:has-text("Confirm")',
      '.VfPpkd-LgbsSe',
    ];

    for (const sel of confirmOffSelectors) {
      if (await page.isVisible(sel).catch(() => false)) {
        await page.click(sel);
        logger('  ✅ 已确认关闭');
        break;
      }
    }

    logger('  等待关闭两步验证完成...');
    await page.waitForTimeout(6000 + Math.random() * 2000);

    logger('  重新打开页面以开启新两步验证...');
    await page.reload({ waitUntil: 'domcontentloaded' });
    await page.waitForTimeout(4000);

    logger('  查找开始/添加验证器按钮...');

    const startSelectors = [
      'button:has-text("开始")',
      'a:has-text("开始")',
      'button:has-text("Get started")',
      'a:has-text("Get started")',
      'button:has-text("添加验证器")',
      'a:has-text("添加验证器")',
      'button:has-text("Set up")',
    ];

    let started = false;
    for (const sel of startSelectors) {
      if (await page.isVisible(sel).catch(() => false)) {
        await page.click(sel);
        started = true;
        break;
      }
    }

    if (!started) {
      logger('  [错误] 找不到开始设置按钮');
      await page.screenshot({ path: 'output/debug-start.png' }).catch(() => {});
      return { success: false, error: '找不到开始设置按钮' };
    }

    await page.waitForTimeout(3000 + Math.random() * 1000);

    const nextSelectors = [
      'button:has-text("下一步")',
      'button:has-text("Next")',
      'button:has-text("继续")',
    ];
    for (const sel of nextSelectors) {
      if (await page.isVisible(sel).catch(() => false)) {
        await page.click(sel);
        await page.waitForTimeout(3000);
        break;
      }
    }

    logger('  等待QR码/密钥页面...');
    await page.waitForTimeout(4000 + Math.random() * 2000);

    const setupResult = await setupNewAuthenticator(page, logger);
    if (!setupResult.success) return setupResult;
    const { newSecret } = setupResult;

    logger('  ✅ 2FA修改完成');

    let recoveryResult = { success: true };
    if (targetRecoveryEmail) {
      recoveryResult = await changeRecoveryEmail(page, targetRecoveryEmail, password, logger);
    }

    return {
      success: true,
      newSecret,
      recoveryResult,
    };
  } catch (err) {
    const errorMsg = err.message;
    logger(`  [异常] ${errorMsg}`);
    logger(`  [堆栈] ${err.stack?.substring(0, 500) || '无堆栈信息'}`);
    await page.screenshot({ path: 'output/debug-error.png' }).catch(() => {});
    return { success: false, error: errorMsg };
  }
}
