import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { readFile } from 'node:fs/promises';
import { dirname, extname, resolve, sep } from 'node:path';
import test, { after, before } from 'node:test';
import { fileURLToPath } from 'node:url';

import { chromium } from '../googlemail/node_modules/playwright/index.mjs';
import { getBrowserLaunchOptions } from '../googlemail/src/browser-runtime.mjs';

const repositoryRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const staticRoot = resolve(process.env.FRONTEND_TEST_DIST || resolve(repositoryRoot, 'static'));
const productionContentSecurityPolicy = [
  "default-src 'self'",
  "script-src 'self'",
  "style-src 'self' 'unsafe-inline'",
  "img-src 'self' data: blob:",
  "font-src 'self'",
  "connect-src 'self'",
  "object-src 'none'",
  "base-uri 'none'",
  "frame-ancestors 'self'",
  "form-action 'self'",
].join('; ');
const contentTypes = {
  '.css': 'text/css; charset=utf-8',
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
};

let browser;
let server;
let baseUrl;

function deferred() {
  let resolvePromise;
  let rejectPromise;
  const promise = new Promise((resolveValue, rejectValue) => {
    resolvePromise = resolveValue;
    rejectPromise = rejectValue;
  });

  return {
    promise,
    resolve: resolvePromise,
    reject: rejectPromise,
  };
}

function fakeAccount(overrides = {}) {
  return {
    id: 'fixture-account',
    email: 'fixture@example.test',
    password: 'fixture-password',
    recovery: 'recovery@example.test',
    secret: 'JBSWY3DPEHPK3PXP',
    remark: 'fixture remark',
    status: 'inactive',
    soldStatus: 'unsold',
    createdAt: '2026-09-14 10:00:00',
    ...overrides,
  };
}

function jsonRoute(payload, status = 200) {
  return route => route.fulfill({
    status,
    contentType: 'application/json',
    body: JSON.stringify(payload),
  });
}

function authenticatedStorage() {
  return {
    loginData: JSON.stringify({ timestamp: Date.now() }),
  };
}

async function launchHeadlessBrowser() {
  if (process.env.GOOGLE_MANAGER_CHROME_EXECUTABLE_PATH || process.env.FLASK_ENV === 'production') {
    // A configured release browser must never fall back to an unreviewed local browser.
    return chromium.launch(getBrowserLaunchOptions({ headless: true }));
  }
  const candidates = [
    { name: 'bundled Chromium', options: {} },
    { name: 'Chrome channel', options: { channel: 'chrome' } },
    { name: 'Edge channel', options: { channel: 'msedge' } },
  ];
  const failures = [];

  for (const candidate of candidates) {
    try {
      return await chromium.launch({ ...candidate.options, headless: true });
    } catch (error) {
      failures.push(new Error(`${candidate.name}: ${error.message}`));
    }
  }

  throw new AggregateError(failures, 'No Playwright-compatible browser is available');
}

async function waitForSignal(signal, description, timeout = 3_000) {
  let timer;
  try {
    await Promise.race([
      signal,
      new Promise((_, reject) => {
        timer = setTimeout(
          () => reject(new Error(`Timed out waiting for ${description}`)),
          timeout,
        );
      }),
    ]);
  } finally {
    clearTimeout(timer);
  }
}

function diagnostics(app) {
  const details = [];
  if (app.pageErrors.length > 0) {
    details.push(`page errors: ${app.pageErrors.join(' | ')}`);
  }
  if (app.consoleErrors.length > 0) {
    details.push(`console errors: ${app.consoleErrors.join(' | ')}`);
  }
  if (app.unexpectedRequests.length > 0) {
    details.push(`unexpected API requests: ${app.unexpectedRequests.join(', ')}`);
  }
  return details.length > 0 ? ` (${details.join('; ')})` : '';
}

async function waitForVisible(app, locator, description, timeout = 3_000) {
  try {
    await Promise.race([
      locator.waitFor({ state: 'visible', timeout }),
      app.pageErrorSignal,
    ]);
  } catch (error) {
    throw new Error(`${description}${diagnostics(app)}`, { cause: error });
  }
}

function assertPageClean(app) {
  assert.deepEqual(app.pageErrors, [], 'the page must not raise an uncaught exception');
  assert.deepEqual(
    app.unexpectedRequests,
    [],
    'every /api request must be served by an explicit synthetic route',
  );
}

async function openApp(t, { storage = {}, apiRoutes = {}, path = '/admin' } = {}) {
  const context = await browser.newContext();
  t.after(() => context.close());

  const page = await context.newPage();
  const pageErrors = [];
  const consoleErrors = [];
  const unexpectedRequests = [];
  let rejectPageError;
  const pageErrorSignal = new Promise((_, reject) => {
    rejectPageError = reject;
  });
  pageErrorSignal.catch(() => {});

  page.on('pageerror', error => {
    const message = `${error.name}: ${error.message}`;
    pageErrors.push(message);
    rejectPageError(new Error(message));
  });
  page.on('console', message => {
    if (message.type() === 'error') consoleErrors.push(message.text());
  });
  await page.addInitScript(() => {
    window.__cspViolations = [];
    document.addEventListener('securitypolicyviolation', event => {
      window.__cspViolations.push({
        blockedURI: event.blockedURI,
        violatedDirective: event.violatedDirective,
      });
    });
  });

  await page.route('**/api/**', async route => {
    const request = route.request();
    const pathname = new URL(request.url()).pathname;
    const routeKey = `${request.method()} ${pathname}`;
    const handler = apiRoutes[routeKey];
    if (['POST', 'PUT', 'PATCH', 'DELETE'].includes(request.method()) &&
        request.headers()['x-requested-with'] !== 'XMLHttpRequest') {
      unexpectedRequests.push(`${routeKey} missing X-Requested-With`);
    }

    if (!handler) {
      unexpectedRequests.push(routeKey);
      await route.fulfill({
        status: 500,
        contentType: 'application/json',
        body: JSON.stringify({ success: false, message: `Unexpected test request: ${routeKey}` }),
      });
      return;
    }

    await handler(route, request);
  });

  await page.addInitScript(initialStorage => {
    localStorage.clear();
    for (const [key, value] of Object.entries(initialStorage)) {
      localStorage.setItem(key, value);
    }
  }, storage);

  const app = {
    consoleErrors,
    page,
    pageErrors,
    pageErrorSignal,
    unexpectedRequests,
  };
  await page.goto(`${baseUrl}${path}`, { waitUntil: 'domcontentloaded' });
  return app;
}

before(async () => {
  server = createServer(async (request, response) => {
    try {
      if (request.method !== 'GET') {
        response.writeHead(405).end();
        return;
      }

      const pathname = decodeURIComponent(new URL(request.url, 'http://localhost').pathname);
      const isAppRoute = pathname === '/' || pathname === '/recharge' || pathname === '/admin' || pathname.startsWith('/admin/');
      const relativePath = isAppRoute ? 'index.html' : pathname.slice(1);
      const filePath = resolve(staticRoot, relativePath);
      if (!filePath.startsWith(`${staticRoot}${sep}`)) {
        response.writeHead(403).end();
        return;
      }

      const body = await readFile(filePath);
      response.writeHead(200, {
        'cache-control': 'no-store',
        'content-security-policy': productionContentSecurityPolicy,
        'content-type': contentTypes[extname(filePath)] || 'application/octet-stream',
      });
      response.end(body);
    } catch (error) {
      response.writeHead(error.code === 'ENOENT' ? 404 : 500).end();
    }
  });

  await new Promise((resolveListen, rejectListen) => {
    server.once('error', rejectListen);
    server.listen(0, '127.0.0.1', () => {
      server.off('error', rejectListen);
      resolveListen();
    });
  });

  const address = server.address();
  assert.notEqual(address, null);
  assert.equal(typeof address, 'object');
  baseUrl = `http://127.0.0.1:${address.port}`;
  browser = await launchHeadlessBrowser();
});

after(async () => {
  await browser?.close();
  if (server?.listening) {
    await new Promise((resolveClose, rejectClose) => {
      server.close(error => (error ? rejectClose(error) : resolveClose()));
    });
  }
});

async function openRecharge(testContext, routes = {}, mode = 'mock') {
  const app = await openApp(testContext, {
    path: '/recharge',
    apiRoutes: {
      'GET /api/recharge/config': jsonRoute({ success: true, data: { mode, enabled: true } }),
      'GET /api/recharge/stats/avg-processing-time': jsonRoute({ success: true, data: [] }),
      'GET /api/recharge/agreement': jsonRoute({ success: true, data: { content: 'Synthetic agreement' } }),
      ...routes,
    },
  });
  await waitForVisible(app, app.page.getByRole('heading', { name: '自助充值与订单服务' }), 'public recharge view');
  return app;
}

test('public recharge and admin portals have separate entry points', async testContext => {
  const recharge = await openRecharge(testContext);
  assert.equal(await recharge.page.getByText('GoogleManager 充值中心', { exact: true }).count(), 1);
  assert.equal(await recharge.page.getByRole('button', { name: '账号列表' }).count(), 0);
  assert.equal(await recharge.page.getByRole('button', { name: '从账号库选择账号' }).count(), 0);
  assert.equal(await recharge.page.getByText('选择 Google 资产库账号', { exact: true }).count(), 0);

  const admin = await openApp(testContext, {
    apiRoutes: {
      'GET /api/auth/check': jsonRoute({ success: false, authenticated: false, banned: false }),
    },
  });
  await waitForVisible(admin, admin.page.getByRole('heading', { name: 'GoogleManager' }), 'admin login');
  assert.equal(await admin.page.getByText('请输入管理员密码以访问系统').count(), 1);
  assertPageClean(recharge);
  assertPageClean(admin);
});

test('recharge assets and API calls load under the production content security policy', async testContext => {
  const app = await openRecharge(testContext);
  const policy = await app.page.evaluate(async () => {
    const response = await fetch(window.location.href);
    return response.headers.get('content-security-policy');
  });
  assert.equal(policy, productionContentSecurityPolicy);
  assert.deepEqual(await app.page.evaluate(() => window.__cspViolations), []);
  assertPageClean(app);
});

test('recharge agreement remains available when average-time statistics fail', async testContext => {
  const app = await openRecharge(testContext, {
    'GET /api/recharge/stats/avg-processing-time': jsonRoute({
      success: false,
      message: 'Synthetic stats failure',
    }, 502),
  });

  const agreementCheckbox = app.page.getByRole('checkbox', { name: /我已完整阅读/ });
  await waitForVisible(app, agreementCheckbox, 'recharge agreement checkbox');
  assert.equal(await agreementCheckbox.isDisabled(), false);
  assert.equal(await app.page.getByText('充值服务协议加载失败，请刷新后重试', { exact: true }).count(), 0);
  assertPageClean(app);
});

test('recharge agreement renders safe formatting without executable HTML', async testContext => {
  const agreementHtml = [
    '<h2 class="font-bold fixed" onclick="window.__agreementPwned = true">Synthetic safe heading</h2>',
    '<p>Keep <strong>formatted terms</strong><img src="x" onerror="window.__agreementPwned = true"></p>',
    '<p>First safe line<br>Second safe line</p><hr>',
    '<a href="javascript:window.__agreementPwned = true">Unsafe agreement link</a>',
    '<a href="https://example.test/terms" target="_blank" onclick="window.__agreementPwned = true">Safe agreement link</a>',
    '<svg onload="window.__agreementPwned = true"><script>window.__agreementPwned = true</script></svg>',
    '<script>window.__agreementPwned = true</script>',
  ].join('');
  const app = await openRecharge(testContext, {
    'GET /api/recharge/agreement': jsonRoute({ success: true, data: { content: agreementHtml } }),
    'POST /api/recharge/redeem-codes/validate': jsonRoute({
      success: true,
      data: { plan_type: 'PLUS', is_renewal_supported: false },
    }),
  });

  await app.page.getByPlaceholder(/CDK 卡密（如/).fill('PLUS-AGREEMENT-TEST');
  await app.page.getByRole('button', { name: '验证卡密', exact: true }).click();
  await waitForVisible(app, app.page.getByText('卡密已核验有效'), 'agreement test CDK');
  await app.page.getByRole('button', { name: '《充值用户服务协议与免责声明》' }).click();
  const dialog = app.page.getByRole('dialog', { name: '充值服务协议与须知' });
  await waitForVisible(app, dialog, 'sanitized recharge agreement');
  const safeHeading = dialog.getByRole('heading', { name: 'Synthetic safe heading' });
  assert.equal(await safeHeading.count(), 1);
  assert.equal(await safeHeading.getAttribute('class'), 'font-bold');
  assert.equal(await dialog.locator('strong').filter({ hasText: 'formatted terms' }).count(), 1);
  assert.equal(await dialog.locator('br').count(), 1);
  assert.equal(await dialog.locator('hr').count(), 1);
  assert.equal(await dialog.getByRole('link', { name: 'Unsafe agreement link' }).count(), 0);
  assert.equal(await dialog.getByText('Unsafe agreement link', { exact: true }).count(), 1);
  const safeLink = dialog.getByRole('link', { name: 'Safe agreement link' });
  assert.equal(await safeLink.getAttribute('href'), 'https://example.test/terms');
  assert.equal(await safeLink.getAttribute('rel'), 'noopener noreferrer');
  const agreementContent = dialog.locator('.prose');
  assert.equal(await agreementContent.locator('script, svg, img, iframe, object, embed').count(), 0);
  assert.equal(await agreementContent.locator('[onclick], [onerror], [onload]').count(), 0);
  assert.notEqual(await app.page.evaluate(() => window.__agreementPwned), true);
  assertPageClean(app);
});

test('recharge masks sensitive credentials by default without changing pasted content', async testContext => {
  const app = await openRecharge(testContext, {
    'POST /api/recharge/redeem-codes/validate': jsonRoute({
      success: true,
      data: { plan_type: 'PLUS', is_renewal_supported: false },
    }),
  });
  const cdk = app.page.getByPlaceholder(/CDK 卡密（如/);
  assert.equal(await cdk.getAttribute('type'), 'password');
  await cdk.fill('PLUS-SENSITIVE-CDK');
  await app.page.getByRole('button', { name: '显示CDK 卡密' }).click();
  assert.equal(await cdk.getAttribute('type'), 'text');
  assert.equal(await cdk.inputValue(), 'PLUS-SENSITIVE-CDK');
  await app.page.getByRole('button', { name: '验证卡密', exact: true }).click();
  await waitForVisible(app, app.page.getByText('卡密已核验有效'), 'validated sensitive CDK');

  const credential = app.page.getByPlaceholder('粘贴来自 chatgpt.com/api/auth/session 的完整 JSON，或输入 user@example.com----sk-ant-sid02-xxx');
  const pastedCredential = 'first-line-synthetic\nsecond-line-synthetic';
  await credential.fill(pastedCredential);
  const credentialMask = app.page.locator('[data-sensitive-mask="充值凭证"]');
  await waitForVisible(app, credentialMask, 'cross-browser credential mask');
  assert.equal(await credential.evaluate(element => element.style.color), 'transparent');
  assert.equal((await credentialMask.textContent()).includes('first-line-synthetic'), false);
  assert.equal((await credentialMask.textContent()).includes('\n'), true);
  await credential.selectText();
  assert.match(
    await credential.evaluate(element => getComputedStyle(element, '::selection').color),
    /^(?:transparent|rgba\(0, 0, 0, 0\))$/,
  );
  assert.equal(await credentialMask.isVisible(), true);
  assert.equal((await credentialMask.textContent()).includes('second-line-synthetic'), false);
  await app.page.getByRole('button', { name: '显示充值凭证' }).click();
  assert.equal(await credentialMask.count(), 0);
  assert.equal(await credential.evaluate(element => element.style.color), '');
  assert.equal(await credential.inputValue(), pastedCredential);

  await app.page.getByRole('button', { name: '进度查询', exact: true }).click();
  const lookup = app.page.getByPlaceholder('输入 CDK 卡密或任务编号（如 PLUS-XXXX 或 TK-2026...）');
  assert.equal(await lookup.getAttribute('type'), 'password');
  await app.page.getByRole('button', { name: /^批量查询/ }).click();
  const batch = app.page.getByPlaceholder(/每行输入一个卡密/);
  await batch.fill('PLUS-FIRST\nPLUS-SECOND');
  const batchMask = app.page.locator('[data-sensitive-mask="批量卡密"]');
  await waitForVisible(app, batchMask, 'cross-browser batch CDK mask');
  assert.equal(await batch.evaluate(element => element.style.color), 'transparent');
  assert.equal((await batchMask.textContent()).includes('PLUS-FIRST'), false);
  assert.equal(await batch.inputValue(), 'PLUS-FIRST\nPLUS-SECOND');

  await app.page.getByRole('button', { name: '账单续费', exact: true }).click();
  const billingCredential = app.page.getByPlaceholder('输入账号 accessToken 或 chatgpt.com/api/auth/session 返回内容');
  assert.equal(await billingCredential.getAttribute('type'), 'password');
  assertPageClean(app);
});

test('recharge task actions require credentials instead of query response secrets', async testContext => {
  const recallStarted = deferred();
  const releaseRecall = deferred();
  testContext.after(() => releaseRecall.resolve());
  let recallPayload;
  let recallRequests = 0;
  const app = await openRecharge(testContext, {
    'GET /api/recharge/tasks/TK-REDACTED-UI': jsonRoute({ success: true, data: {
      task_no: 'TK-REDACTED-UI', plan_type: 'PLUS', status: 'processing', status_text: '处理中',
    } }),
    'POST /api/recharge/tasks/recall': async (route, request) => {
      recallRequests += 1;
      recallPayload = request.postDataJSON();
      recallStarted.resolve();
      await releaseRecall.promise;
      await jsonRoute({ success: true, data: {
        task_no: 'TK-REDACTED-UI', plan_type: 'PLUS', status: 'recalled', status_text: '已撤回',
      } })(route);
    },
  });

  await app.page.getByRole('button', { name: '进度查询', exact: true }).click();
  await app.page.getByPlaceholder('输入 CDK 卡密或任务编号（如 PLUS-XXXX 或 TK-2026...）').fill('TK-REDACTED-UI');
  await app.page.getByRole('button', { name: '立即查询', exact: true }).click();
  await waitForVisible(app, app.page.getByText('处理中', { exact: true }), 'redacted task result');
  assert.equal(await app.page.getByText('接收邮箱', { exact: true }).count(), 0);
  assert.equal(await app.page.getByText(/^卡密：/).count(), 0);

  await app.page.getByRole('button', { name: '撤回任务', exact: true }).click();
  await waitForVisible(app, app.page.getByText('目标任务：'), 'bound task action');
  const actionDialog = app.page.getByRole('dialog', { name: '确认撤回该充值任务？' });
  assert.equal(await actionDialog.getByText('TK-REDACTED-UI', { exact: true }).count(), 1);
  const redeemCodeInput = app.page.getByPlaceholder('请输入完整卡密');
  const emailInput = app.page.getByPlaceholder('user@gmail.com');
  assert.equal(await redeemCodeInput.inputValue(), '');
  assert.equal(await redeemCodeInput.getAttribute('type'), 'password');
  assert.equal(await emailInput.inputValue(), '');
  await redeemCodeInput.fill('PLUS-SYNTHETIC-RECALL');
  await emailInput.fill('owner@example.test');
  const confirmAction = app.page.getByRole('button', { name: '确认执行', exact: true });
  await confirmAction.evaluate(button => {
    button.click();
    button.click();
  });
  await waitForSignal(recallStarted.promise, 'recall task request');
  assert.equal(recallRequests, 1, 'a rapid double click must create only one task mutation');
  assert.equal(await redeemCodeInput.isDisabled(), true);
  assert.equal(await emailInput.isDisabled(), true);
  releaseRecall.resolve();
  await waitForVisible(app, app.page.getByText('已撤回', { exact: true }), 'recalled task state');
  assert.deepEqual(recallPayload, {
    redeem_code: 'PLUS-SYNTHETIC-RECALL',
    email: 'owner@example.test',
    confirmed: true,
    task_no: 'TK-REDACTED-UI',
  });
  assertPageClean(app);
});

test('recharge clears and disables renewal when the validated CDK does not support it', async testContext => {
  const app = await openRecharge(testContext, {
    'POST /api/recharge/redeem-codes/validate': async (route, request) => {
      const { redeem_code: redeemCode } = request.postDataJSON();
      await jsonRoute({ success: true, data: {
        plan_type: 'PLUS',
        is_renewal_supported: redeemCode === 'PLUS-RENEW-YES',
      } })(route);
    },
  });
  const cdkInput = app.page.getByPlaceholder(/CDK 卡密（如/);
  const renewal = app.page.getByRole('checkbox', { name: /仅续费模式/ });

  await cdkInput.fill('PLUS-RENEW-YES');
  await app.page.getByRole('button', { name: '验证卡密', exact: true }).click();
  await waitForVisible(app, app.page.getByText('卡密已核验有效'), 'renewal-supported CDK');
  assert.equal(await renewal.isDisabled(), false);
  await renewal.check();
  assert.equal(await renewal.isChecked(), true);

  await cdkInput.fill('PLUS-RENEW-NO');
  assert.equal(await renewal.isChecked(), false, 'changing the CDK must clear the previous renewal choice');
  await app.page.getByRole('button', { name: '验证卡密', exact: true }).click();
  await waitForVisible(app, app.page.getByText('卡密已核验有效'), 'renewal-unsupported CDK');
  assert.equal(await renewal.isDisabled(), true);
  assert.equal(await renewal.isChecked(), false);
  assertPageClean(app);
});

test('editing a CDK while validation is pending re-enables validation', async testContext => {
  const firstValidationStarted = deferred();
  const releaseFirstValidation = deferred();
  testContext.after(() => releaseFirstValidation.resolve());
  let validationCount = 0;
  const app = await openRecharge(testContext, {
    'POST /api/recharge/redeem-codes/validate': async route => {
      validationCount += 1;
      if (validationCount === 1) {
        firstValidationStarted.resolve();
        await releaseFirstValidation.promise;
      }
      try {
        await jsonRoute({ success: true, data: { plan_type: 'PLUS', plan_name: 'Fresh validation' } })(route);
      } catch {
        // The first request may be aborted after the input changes.
      }
    },
  });
  const input = app.page.getByPlaceholder(/CDK 卡密（如/);
  const validateButton = app.page.getByRole('button', { name: '验证卡密', exact: true });

  await input.fill('PLUS-FIRST-PENDING');
  await validateButton.click();
  await waitForSignal(firstValidationStarted.promise, 'first CDK validation request');
  await input.fill('PLUS-SECOND-INPUT');
  assert.equal(await validateButton.isDisabled(), false);
  releaseFirstValidation.resolve();
  await new Promise(resolve => setTimeout(resolve, 100));
  assert.equal(await validateButton.isDisabled(), false);
  assertPageClean(app);
});

test('recharge keeps renewal disabled when upstream omits its capability flag', async testContext => {
  const app = await openRecharge(testContext, {
    'POST /api/recharge/redeem-codes/validate': jsonRoute({ success: true, data: {
      plan_type: 'PLUS',
    } }),
  });
  const cdkInput = app.page.getByPlaceholder(/CDK 卡密（如/);
  const renewal = app.page.getByRole('checkbox', { name: /仅续费模式/ });

  await cdkInput.fill('PLUS-RENEW-UNKNOWN');
  await app.page.getByRole('button', { name: '验证卡密', exact: true }).click();
  await waitForVisible(app, app.page.getByText('卡密已核验有效'), 'renewal capability omission');
  assert.equal(await renewal.isDisabled(), true);
  assert.equal(await renewal.isChecked(), false);
  assertPageClean(app);
});

test('recharge refreshes the parsed email and clears confirmation when credentials change', async testContext => {
  const app = await openRecharge(testContext, {
    'POST /api/recharge/redeem-codes/validate': jsonRoute({
      success: true,
      data: { plan_type: 'PLUS', is_renewal_supported: true },
    }),
  });
  const credential = app.page.getByPlaceholder('粘贴来自 chatgpt.com/api/auth/session 的完整 JSON，或输入 user@example.com----sk-ant-sid02-xxx');
  const email = app.page.getByPlaceholder('user@gmail.com', { exact: true });
  const ownership = app.page.getByRole('checkbox', { name: /我已仔细核对/ });
  const first = JSON.stringify({ accessToken: 'synthetic-a', user: { email: 'first@example.test' } });
  const second = JSON.stringify({ accessToken: 'synthetic-b', user: { email: 'second@example.test' } });

  await app.page.getByPlaceholder(/CDK 卡密（如/).fill('PLUS-CREDENTIAL-TEST');
  await app.page.getByRole('button', { name: '验证卡密', exact: true }).click();
  await waitForVisible(app, app.page.getByText('卡密已核验有效'), 'credential-change CDK');
  await credential.fill(first);
  await app.page.waitForFunction(() => document.querySelector('input[placeholder="user@gmail.com"]')?.value === 'first@example.test');
  await ownership.check();
  assert.equal(await ownership.isChecked(), true);

  await credential.fill(second);
  await app.page.waitForFunction(() => document.querySelector('input[placeholder="user@gmail.com"]')?.value === 'second@example.test');
  assert.equal(await email.inputValue(), 'second@example.test');
  assert.equal(await ownership.isChecked(), false);
  assertPageClean(app);
});

test('recharge submission binds identical credentials and clears them after success', async testContext => {
  let challengePayload;
  let invoicePayload;
  const invoiceRequested = deferred();
  const task = { task_no: 'TK-MOCK-UI', redeem_code: 'PLUS-SYNTHETIC-UI', plan_type: 'PLUS', status: 'completed', is_mock: true };
  const app = await openRecharge(testContext, {
    'POST /api/recharge/redeem-codes/validate': jsonRoute({ success: true, data: { plan_type: 'PLUS' } }),
    'POST /api/recharge/submission-challenges': async (route, request) => {
      challengePayload = request.postDataJSON();
      await jsonRoute({ success: true, data: { challenge_token: 'synthetic-challenge' } })(route);
    },
    'POST /api/recharge/tasks': async (route, request) => {
      assert.equal(request.postDataJSON().token_input, challengePayload.token_input);
      await jsonRoute({ success: true, data: task }, 201)(route);
    },
    'GET /api/recharge/tasks/TK-MOCK-UI': jsonRoute({ success: true, data: task }),
    'POST /api/recharge/tasks/lookup': jsonRoute({ success: true, data: task }),
    'POST /api/recharge/tasks/invoice/download': async (route, request) => {
      assert.equal(new URL(request.url()).search, '');
      invoicePayload = request.postDataJSON();
      invoiceRequested.resolve();
      await route.fulfill({
        status: 200,
        contentType: 'text/plain; charset=utf-8',
        body: 'Synthetic invoice receipt',
      });
    },
  });
  await app.page.getByPlaceholder(/CDK 卡密（如/).fill(task.redeem_code);
  await app.page.getByRole('button', { name: '验证卡密', exact: true }).click();
  await waitForVisible(app, app.page.getByText('卡密已核验有效'), 'verified CDK');
  const credential = app.page.getByPlaceholder('粘贴来自 chatgpt.com/api/auth/session 的完整 JSON，或输入 user@example.com----sk-ant-sid02-xxx');
  await credential.fill('synthetic-not-real-credential');
  await app.page.getByPlaceholder('user@gmail.com', { exact: true }).fill('用户@example.中国');
  await app.page.getByRole('checkbox', { name: /我已仔细核对/ }).check();
  await app.page.getByRole('checkbox', { name: /我已完整阅读/ }).check();
  await app.page.getByRole('button', { name: '同意协议并提交充值任务' }).click();
  await waitForVisible(app, app.page.getByText('任务提交成功！', { exact: true }), 'created task');
  assert.equal(await credential.inputValue(), '');
  await app.page.getByRole('button', { name: '查看履约进度' }).click();
  await waitForVisible(
    app,
    app.page.getByText('请使用完整卡密查询后下载对账凭证', { exact: true }),
    'task-number invoice restriction',
  );
  assert.equal(await app.page.getByRole('button', { name: '对账凭证', exact: true }).count(), 0);

  const lookupInput = app.page.getByPlaceholder('输入 CDK 卡密或任务编号（如 PLUS-XXXX 或 TK-2026...）');
  await lookupInput.fill(task.redeem_code);
  await app.page.getByRole('button', { name: '立即查询', exact: true }).click();
  const receipt = app.page.getByRole('button', { name: '对账凭证', exact: true });
  await waitForVisible(app, receipt, 'redeem-code invoice button');
  await receipt.click();
  await waitForSignal(invoiceRequested.promise, 'invoice POST request');
  assert.deepEqual(invoicePayload, { redeem_code: task.redeem_code });
  assertPageClean(app);
});

test('recharge polling retries transient failures and stops at a terminal state', async testContext => {
  let requests = 0;
  const completed = deferred();
  const app = await openRecharge(testContext, {
    'GET /api/recharge/tasks/TK-MOCK-POLL': async route => {
      requests += 1;
      if (requests === 2) {
        await jsonRoute({ success: false, message: 'Synthetic upstream timeout' }, 502)(route);
        return;
      }
      const terminal = requests >= 3;
      await jsonRoute({ success: true, data: {
        task_no: 'TK-MOCK-POLL', plan_type: 'PLUS', status: terminal ? 'completed' : 'unknown',
        status_text: terminal ? '已核对完成' : '等待核对', is_mock: true,
      } })(route);
      if (terminal) completed.resolve();
    },
  });
  await app.page.getByRole('button', { name: '进度查询', exact: true }).click();
  await app.page.getByPlaceholder('输入 CDK 卡密或任务编号（如 PLUS-XXXX 或 TK-2026...）').fill('TK-MOCK-POLL');
  await app.page.getByRole('button', { name: '立即查询', exact: true }).click();
  await waitForVisible(app, app.page.getByText('等待核对', { exact: true }), 'initial lookup state');
  await waitForSignal(completed.promise, 'retry after a transient recharge failure', 22000);
  await waitForVisible(app, app.page.getByText('已核对完成', { exact: true }), 'terminal lookup state');
  await new Promise(resolve => setTimeout(resolve, 5500));
  assert.equal(requests, 3);
  assertPageClean(app);
});

test('recharge polling stops when switching internal tabs', async testContext => {
  let requests = 0;
  const app = await openRecharge(testContext, {
    'GET /api/recharge/tasks/TK-MOCK-TAB': async route => {
      requests += 1;
      await jsonRoute({ success: true, data: {
        task_no: 'TK-MOCK-TAB', plan_type: 'PLUS', status: 'processing',
        status_text: '测试处理中', is_mock: true,
      } })(route);
    },
  });
  await app.page.getByRole('button', { name: '进度查询', exact: true }).click();
  await app.page.getByPlaceholder('输入 CDK 卡密或任务编号（如 PLUS-XXXX 或 TK-2026...）').fill('TK-MOCK-TAB');
  await app.page.getByRole('button', { name: '立即查询', exact: true }).click();
  await waitForVisible(app, app.page.getByText('测试处理中', { exact: true }), 'initial lookup result');
  await app.page.getByRole('button', { name: '账单续费', exact: true }).click();
  await new Promise(resolve => setTimeout(resolve, 5500));
  assert.equal(requests, 1, 'leaving the lookup tab cancels its polling timer');
  assertPageClean(app);
});

test('a stale batch lookup cannot clear a newer single lookup or overwrite its result', async testContext => {
  const batchStarted = deferred();
  const releaseBatch = deferred();
  const singleStarted = deferred();
  const releaseSingle = deferred();
  testContext.after(() => {
    releaseBatch.resolve();
    releaseSingle.resolve();
  });
  const app = await openRecharge(testContext, {
    'POST /api/recharge/tasks/lookup-batch': async route => {
      batchStarted.resolve();
      await releaseBatch.promise;
      try {
        await jsonRoute({ success: true, data: [{
          task_no: 'TK-STALE-BATCH',
          plan_type: 'PLUS',
          status: 'completed',
          status_text: '过期批量结果',
        }] })(route);
      } catch {
        // The browser may abort the request when switching lookup modes.
      }
    },
    'POST /api/recharge/tasks/lookup': async route => {
      singleStarted.resolve();
      await releaseSingle.promise;
      await jsonRoute({ success: true, data: {
        task_no: 'TK-FRESH-SINGLE',
        plan_type: 'PLUS',
        status: 'completed',
        status_text: '最新单查结果',
        is_mock: true,
      } })(route);
    },
  });

  await app.page.getByRole('button', { name: '进度查询', exact: true }).click();
  await app.page.getByRole('button', { name: /^批量查询/ }).click();
  await app.page.getByPlaceholder(/每行输入一个卡密/).fill('PLUS-BATCH-STALE');
  await app.page.getByRole('button', { name: '批量查询进度', exact: true }).click();
  await waitForSignal(batchStarted.promise, 'batch lookup request');

  await app.page.getByRole('button', { name: '单个查询', exact: true }).click();
  await app.page.getByPlaceholder('输入 CDK 卡密或任务编号（如 PLUS-XXXX 或 TK-2026...）').fill('PLUS-FRESH-SINGLE');
  await app.page.getByRole('button', { name: '立即查询', exact: true }).click();
  await waitForSignal(singleStarted.promise, 'single lookup request');

  releaseBatch.resolve();
  await new Promise(resolve => setTimeout(resolve, 100));
  assert.equal(
    await app.page.getByRole('button', { name: '立即查询', exact: true }).isDisabled(),
    true,
    'the stale batch request must not clear the newer single lookup loading state',
  );

  releaseSingle.resolve();
  await waitForVisible(app, app.page.getByText('最新单查结果', { exact: true }), 'fresh single lookup result');
  assert.equal(await app.page.getByText('过期批量结果', { exact: true }).count(), 0);
  assertPageClean(app);
});

test('editing a single lookup input cancels the request and clears its loading state', async testContext => {
  const requestStarted = deferred();
  const releaseRequest = deferred();
  testContext.after(() => releaseRequest.resolve());
  const app = await openRecharge(testContext, {
    'POST /api/recharge/tasks/lookup': async route => {
      requestStarted.resolve();
      await releaseRequest.promise;
      try {
        await jsonRoute({ success: true, data: {
          task_no: 'TK-STALE-SINGLE', plan_type: 'PLUS', status: 'completed', status_text: '过期单查结果',
        } })(route);
      } catch {
        // The request is expected to be aborted when the input changes.
      }
    },
  });

  await app.page.getByRole('button', { name: '进度查询', exact: true }).click();
  const input = app.page.getByPlaceholder('输入 CDK 卡密或任务编号（如 PLUS-XXXX 或 TK-2026...）');
  const lookupButton = app.page.getByRole('button', { name: '立即查询', exact: true });
  await input.fill('PLUS-FIRST-LOOKUP');
  await lookupButton.click();
  await waitForSignal(requestStarted.promise, 'single lookup request');
  await input.fill('PLUS-SECOND-LOOKUP');
  assert.equal(await lookupButton.isDisabled(), false);
  releaseRequest.resolve();
  await new Promise(resolve => setTimeout(resolve, 100));
  assert.equal(await lookupButton.isDisabled(), false);
  assert.equal(await app.page.getByText('过期单查结果', { exact: true }).count(), 0);
  assertPageClean(app);
});

test('editing batch lookup input cancels stale responses and clears old results', async testContext => {
  const secondRequestStarted = deferred();
  const releaseSecondRequest = deferred();
  testContext.after(() => releaseSecondRequest.resolve());
  let requestCount = 0;
  const app = await openRecharge(testContext, {
    'POST /api/recharge/tasks/lookup-batch': async route => {
      requestCount += 1;
      if (requestCount === 1) {
        await jsonRoute({ success: true, data: [{
          task_no: 'TK-OLD-BATCH', plan_type: 'PLUS', status: 'completed', status_text: '旧批量结果', created_at: '-', ok: true,
        }] })(route);
        return;
      }
      secondRequestStarted.resolve();
      await releaseSecondRequest.promise;
      try {
        await jsonRoute({ success: true, data: [{
          task_no: 'TK-STALE-BATCH-INPUT', plan_type: 'PLUS', status: 'completed', status_text: '过期输入结果', created_at: '-', ok: true,
        }] })(route);
      } catch {
        // The second request may be aborted after the textarea changes.
      }
    },
  });

  await app.page.getByRole('button', { name: '进度查询', exact: true }).click();
  await app.page.getByRole('button', { name: /^批量查询/ }).click();
  const input = app.page.getByPlaceholder(/每行输入一个卡密/);
  const batchButton = app.page.getByRole('button', { name: '批量查询进度', exact: true });
  await input.fill('PLUS-FIRST-BATCH');
  await batchButton.click();
  await waitForVisible(app, app.page.getByText('TK-OLD-BATCH', { exact: true }), 'initial batch result');

  await input.fill('PLUS-SECOND-BATCH');
  await batchButton.click();
  await waitForSignal(secondRequestStarted.promise, 'second batch lookup request');
  await input.fill('PLUS-THIRD-BATCH');
  assert.equal(await batchButton.isDisabled(), false);
  assert.equal(await app.page.getByText('TK-OLD-BATCH', { exact: true }).count(), 0);

  releaseSecondRequest.resolve();
  await new Promise(resolve => setTimeout(resolve, 100));
  assert.equal(await app.page.getByText('过期输入结果', { exact: true }).count(), 0);
  assertPageClean(app);
});

test('recharge discards a billing result after its credential changes', async testContext => {
  const requested = deferred();
  const release = deferred();
  const app = await openRecharge(testContext, {
    'POST /api/recharge/billing/query': async route => {
      requested.resolve();
      await release.promise;
      await jsonRoute({ success: true, data: { plan_name: 'Stale synthetic plan', is_mock: true } })(route);
    },
  });
  await app.page.getByRole('button', { name: '账单续费', exact: true }).click();
  const credential = app.page.getByPlaceholder('输入账号 accessToken 或 chatgpt.com/api/auth/session 返回内容');
  await credential.fill('synthetic-old-token');
  await app.page.getByRole('button', { name: '查询账单状态', exact: true }).click();
  await waitForSignal(requested.promise, 'billing request');
  await credential.fill('synthetic-new-token');
  release.resolve();
  await new Promise(resolve => setTimeout(resolve, 250));
  assert.equal(await app.page.getByRole('heading', { name: 'Stale synthetic plan' }).count(), 0);
  assertPageClean(app);
});

test('recharge serializes billing mutations before refreshing status', async testContext => {
  const cancellationStarted = deferred();
  const releaseCancellation = deferred();
  const refreshStarted = deferred();
  const releaseRefresh = deferred();
  testContext.after(() => {
    releaseCancellation.resolve();
    releaseRefresh.resolve();
  });
  let queryCount = 0;
  let cancellationCompleted = false;
  const app = await openRecharge(testContext, {
    'POST /api/recharge/billing/query': async route => {
      queryCount += 1;
      if (queryCount === 2) {
        refreshStarted.resolve();
        await releaseRefresh.promise;
      }
      await jsonRoute({ success: true, data: {
        plan_name: 'Race synthetic plan',
        auto_renew: queryCount === 1 || !cancellationCompleted,
        is_mock: true,
        invoices: [],
      } })(route);
    },
    'POST /api/recharge/billing/cancel-subscription': async route => {
      cancellationStarted.resolve();
      await releaseCancellation.promise;
      cancellationCompleted = true;
      await jsonRoute({ success: true, data: {
        success: true,
        auto_renew: false,
        is_mock: true,
      } })(route);
    },
  });

  await app.page.getByRole('button', { name: '账单续费', exact: true }).click();
  await app.page.getByPlaceholder('输入账号 accessToken 或 chatgpt.com/api/auth/session 返回内容').fill('synthetic-race-token');
  await app.page.getByRole('button', { name: '查询账单状态', exact: true }).click();
  await waitForVisible(app, app.page.getByRole('heading', { name: 'Race synthetic plan' }), 'initial billing result');
  assert.equal(queryCount, 1);

  app.page.once('dialog', dialog => dialog.accept());
  await app.page.getByRole('button', { name: '取消自动续费', exact: true }).click();
  await waitForSignal(cancellationStarted.promise, 'billing cancellation request');
  await app.page.waitForFunction(() => Array.from(document.querySelectorAll('button'))
    .some(button => button.textContent?.includes('查询账单状态') && button.disabled));
  assert.equal(await app.page.getByRole('button', { name: '查询账单状态', exact: true }).isDisabled(), true);

  releaseCancellation.resolve();
  await waitForSignal(refreshStarted.promise, 'billing refresh after cancellation');
  assert.equal(
    await app.page.getByRole('button', { name: '查询账单状态', exact: true }).isDisabled(),
    true,
    'the mutation lock must remain active until the billing refresh finishes',
  );
  assert.equal(await app.page.getByRole('button', { name: '取消自动续费', exact: true }).count(), 0,
    'refreshing must hide the previous actionable subscription result');
  assert.equal(await app.page.getByPlaceholder(/输入账号 accessToken/).isDisabled(), true);
  releaseRefresh.resolve();
  await waitForVisible(app, app.page.getByText('已关闭自动续费', { exact: true }), 'refreshed billing status');
  assert.equal(queryCount, 2, 'a successful mutation should trigger exactly one status refresh');
  assertPageClean(app);
});

test('mock billing invoice download posts its identifier instead of exposing it in the URL', async testContext => {
  const invoiceRequested = deferred();
  let invoicePayload;
  const app = await openRecharge(testContext, {
    'POST /api/recharge/billing/query': jsonRoute({ success: true, data: {
      plan_name: 'Invoice synthetic plan',
      auto_renew: false,
      is_mock: true,
      invoices: [{ id: 'invoice-001', slug: 'private-invoice-slug', date: '2026-09-20', amount: '$20.00' }],
    } }),
    'POST /api/recharge/tasks/invoice/download': async (route, request) => {
      assert.equal(new URL(request.url()).search, '');
      invoicePayload = request.postDataJSON();
      invoiceRequested.resolve();
      await route.fulfill({
        status: 200,
        contentType: 'text/plain; charset=utf-8',
        body: 'Synthetic billing receipt',
      });
    },
  });

  await app.page.getByRole('button', { name: '账单续费', exact: true }).click();
  await app.page.getByPlaceholder('输入账号 accessToken 或 chatgpt.com/api/auth/session 返回内容').fill('synthetic-invoice-token');
  await app.page.getByRole('button', { name: '查询账单状态', exact: true }).click();
  const downloadButton = app.page.getByRole('button', { name: '下载收据 (TXT)', exact: true });
  await waitForVisible(app, downloadButton, 'mock billing receipt download');
  assert.equal(await app.page.getByRole('link', { name: '下载收据 (TXT)', exact: true }).count(), 0);
  await downloadButton.click();
  await waitForSignal(invoiceRequested.promise, 'billing invoice POST request');
  assert.deepEqual(invoicePayload, { slug: 'private-invoice-slug' });
  assertPageClean(app);
});

test('recharge live mode does not offer unverified invoice downloads', async testContext => {
  const app = await openRecharge(testContext, {
    'GET /api/recharge/tasks/TK-LIVE-UI': jsonRoute({ success: true, data: {
      task_no: 'TK-LIVE-UI', plan_type: 'PLUS', status: 'completed', is_mock: false,
    } }),
    'POST /api/recharge/billing/query': jsonRoute({ success: true, data: {
      plan_name: 'Synthetic plan', auto_renew: true, is_mock: false,
      invoices: [{ id: 'remote-invoice', slug: 'remote-invoice', date: 'synthetic' }],
    } }),
  }, 'live');
  await app.page.getByRole('button', { name: '进度查询', exact: true }).click();
  await app.page.getByPlaceholder('输入 CDK 卡密或任务编号（如 PLUS-XXXX 或 TK-2026...）').fill('TK-LIVE-UI');
  await app.page.getByRole('button', { name: '立即查询', exact: true }).click();
  await waitForVisible(app, app.page.getByRole('heading', { name: 'PLUS 履约任务' }), 'live task');
  assert.equal(await app.page.getByRole('link', { name: '对账凭证' }).count(), 0);
  await app.page.getByRole('button', { name: '账单续费', exact: true }).click();
  await app.page.getByPlaceholder('输入账号 accessToken 或 chatgpt.com/api/auth/session 返回内容').fill('synthetic-billing-credential');
  await app.page.getByRole('button', { name: '查询账单状态', exact: true }).click();
  await waitForVisible(app, app.page.getByRole('heading', { name: 'Synthetic plan' }), 'live billing result');
  assert.equal(await app.page.getByRole('link', { name: /下载收据/ }).count(), 0);
  await app.page.getByRole('button', { name: '进度查询', exact: true }).click();
  await app.page.getByRole('button', { name: '账单续费', exact: true }).click();
  assert.equal(await app.page.getByPlaceholder('输入账号 accessToken 或 chatgpt.com/api/auth/session 返回内容').inputValue(), '');
  assert.equal(await app.page.getByRole('heading', { name: 'Synthetic plan' }).count(), 0);
  assertPageClean(app);
});

async function openBatchImport(testContext, batchHandler) {
  const app = await openApp(testContext, {
    apiRoutes: {
      'GET /api/auth/check': jsonRoute({ success: true, authenticated: true, banned: false }),
      'GET /api/accounts': jsonRoute({ success: true, data: [] }),
      'POST /api/accounts/batch': batchHandler,
    },
  });
  await waitForVisible(app, app.page.getByRole('heading', { name: '账号库' }), 'account library did not load');
  await app.page.getByRole('button', { name: '导入账号', exact: true }).click();
  await app.page.getByRole('button', { name: '批量导入', exact: true }).click();
  return app;
}

for (const delimiter of ['|', '——', '----', '--']) {
  test(`batch import preserves fields using ${delimiter} delimiters`, async testContext => {
    const submitted = deferred();
    const app = await openBatchImport(testContext, async (route, request) => {
      submitted.resolve(request.postDataJSON());
      await jsonRoute({ success: false, message: 'fixture response' })(route);
    });
    const accounts = Array.from({ length: delimiter === '|' ? 6 : 1 }, (unused, index) => ({
      email: `batch-${index + 1}@example.test`,
      password: delimiter === '|' ? (index === 5 ? 'Fixture----Password' : 'FixturePassword') : 'Fixture|Password',
      recovery: `recovery-${index + 1}@example.test`,
      secret: 'JBSWY3DPEHPK3PXP',
      remark: 'UnitedStates',
    }));
    const text = accounts.map(account => Object.values(account).join(delimiter)).join('\r\n');
    await app.page.locator('textarea').fill(text);
    await app.page.getByRole('button', { name: /立即导入/ }).click();
    await waitForSignal(submitted.promise, 'batch payload');
    const payload = await submitted.promise;
    assert.deepEqual(payload.accounts, accounts);
    assertPageClean(app);
  });
}

test('batch import blocks invalid lines without exposing raw credentials', async testContext => {
  const app = await openBatchImport(testContext, jsonRoute({ success: false }));
  const rawCredential = 'FIXTURE-NOT-FOR-PREVIEW';
  const text = `valid@example.test----FixturePassword\ninvalid@example.test,${rawCredential},invalid`;
  await app.page.locator('textarea').fill(text);
  assert.equal(await app.page.getByRole('button', { name: /立即导入/ }).isDisabled(), true);
  await waitForVisible(app, app.page.getByRole('alert'), 'invalid line feedback was not shown');
  assert.match(await app.page.getByRole('alert').innerText(), /第 2 行/);
  assert.equal((await app.page.locator('body').innerText()).includes(rawCredential), false);
  assertPageClean(app);
});

test('batch import retains input when every row is rejected', async testContext => {
  const app = await openBatchImport(testContext, jsonRoute({
    success: true,
    data: { success_count: 0, failed_count: 1, failed_emails: ['duplicate@example.test'], accounts: [] },
  }));
  const text = 'duplicate@example.test----FixturePassword';
  await app.page.locator('textarea').fill(text);
  await app.page.getByRole('button', { name: /立即导入/ }).click();
  await waitForVisible(app, app.page.getByText(/1 个账号重复或格式无效/), 'rejection feedback was not shown');
  assert.equal(await app.page.getByRole('heading', { name: '导入账号', exact: true }).isVisible(), true);
  assert.equal(await app.page.locator('textarea').inputValue(), text);
  assertPageClean(app);
});

test('batch import disables resubmission while the request is pending', async testContext => {
  const submitted = deferred();
  const release = deferred();
  const app = await openBatchImport(testContext, async route => {
    submitted.resolve();
    await release.promise;
    await jsonRoute({ success: false, message: 'fixture response' })(route);
  });
  try {
    await app.page.locator('textarea').fill('pending@example.test----FixturePassword');
    await app.page.getByRole('button', { name: /立即导入/ }).click();
    await waitForSignal(submitted.promise, 'pending batch request');
    assert.equal(await app.page.getByRole('button', { name: /导入/ }).last().isDisabled(), true);
    assertPageClean(app);
  } finally {
    release.resolve();
  }
});

test('a newer notification remains visible for its full duration', async testContext => {
  let importCount = 0;
  const app = await openBatchImport(testContext, async route => {
    importCount += 1;
    await jsonRoute({
      success: true,
      data: {
        success_count: 0,
        failed_count: 1,
        failed_emails: [`failed-${importCount}@example.test`],
        accounts: [],
      },
    })(route);
  });

  await app.page.locator('textarea').fill('first@example.test----FixturePassword');
  await app.page.getByRole('button', { name: /立即导入/ }).click();
  await waitForVisible(app, app.page.getByText(/failed-1@example\.test/), 'the first import notification was not shown');
  await new Promise(resolve => setTimeout(resolve, 1000));

  await app.page.locator('textarea').fill('second@example.test----FixturePassword');
  await app.page.getByRole('button', { name: /立即导入/ }).click();
  await waitForVisible(app, app.page.getByText(/failed-2@example\.test/), 'the second import notification was not shown');
  await new Promise(resolve => setTimeout(resolve, 2200));

  assert.equal(await app.page.getByText(/failed-2@example\.test/).count(), 1);
  assertPageClean(app);
});

test('malformed darkMode storage still renders the login page', async testContext => {
  const app = await openApp(testContext, {
    storage: { darkMode: '{malformed-json' },
    apiRoutes: {
      'GET /api/auth/check': jsonRoute({
        success: true,
        authenticated: false,
        banned: false,
      }),
    },
  });

  await waitForVisible(
    app,
    app.page.getByRole('heading', { name: 'GoogleManager' }),
    'malformed darkMode storage prevented the login page from rendering',
  );
  await waitForVisible(
    app,
    app.page.getByPlaceholder('请输入密码'),
    'the administrator password input was not rendered',
  );
  assertPageClean(app);
});

test('an authenticated server session restores the account library without loginData', async testContext => {
  const account = fakeAccount({ email: 'server-session@example.test' });
  const app = await openApp(testContext, {
    apiRoutes: {
      'GET /api/auth/check': jsonRoute({
        success: true,
        authenticated: true,
        banned: false,
      }),
      'GET /api/accounts': jsonRoute({ success: true, data: [account] }),
    },
  });

  await waitForVisible(
    app,
    app.page.getByText(account.email, { exact: true }),
    'the authenticated server session did not restore the account library',
  );
  await waitForVisible(
    app,
    app.page.getByRole('heading', { name: '账号库' }),
    'the account library heading was not rendered',
  );
  assertPageClean(app);
});

test('Gmail OAuth configuration errors are shown in the inbox view', async testContext => {
  const app = await openApp(testContext, {
    apiRoutes: {
      'GET /api/auth/check': jsonRoute({
        success: true,
        authenticated: true,
        banned: false,
      }),
      'GET /api/accounts': jsonRoute({ success: true, data: [] }),
      'GET /api/gmail/connections': jsonRoute({ success: true, data: [] }),
      'GET /api/gmail/oauth/start': jsonRoute({
        success: false,
        data: null,
        message: '未配置有效的 GMAIL_CLIENT_SECRET_FILE',
      }, 503),
    },
  });

  await app.page.getByRole('button', { name: 'Gmail 收件箱' }).click();
  await waitForVisible(
    app,
    app.page.getByRole('heading', { name: 'Gmail 收件箱' }),
    'the Gmail inbox view did not render',
  );
  await app.page.getByRole('button', { name: '授权 Gmail' }).click();
  await waitForVisible(
    app,
    app.page.getByText('未配置有效的 GMAIL_CLIENT_SECRET_FILE', { exact: true }),
    'the Gmail OAuth configuration error was not shown',
  );
  assert.deepEqual(app.pageErrors, [], 'OAuth errors must not become uncaught page errors');
  assertPageClean(app);
});

test('queued Gmail sync reports pending work without refreshing messages immediately', async testContext => {
  let messageRequests = 0;
  const app = await openApp(testContext, {
    apiRoutes: {
      'GET /api/auth/check': jsonRoute({ success: true, authenticated: true, banned: false }),
      'GET /api/accounts': jsonRoute({ success: true, data: [] }),
      'GET /api/gmail/connections': jsonRoute({ success: true, data: [
        { id: 1, email: 'queued-sync@example.test' },
      ] }),
      'GET /api/gmail/1/messages': async route => {
        messageRequests += 1;
        await jsonRoute({ success: true, data: { messages: [], nextPageToken: null } })(route);
      },
      'GET /api/gmail/daemon/status': jsonRoute({ success: true, data: {
        isRunning: false, totalRuns: 0, messagesPolled: 0, lastDurationSeconds: 0,
      } }),
      'POST /api/gmail/daemon/sync-now': jsonRoute({
        success: true,
        data: { taskId: 'gmail-sync-fixture', status: 'pending', createdAt: '2026-09-19T08:00:00Z' },
        message: '同步任务已排队',
      }, 202),
    },
  });

  await app.page.getByRole('button', { name: 'Gmail 收件箱' }).click();
  await waitForVisible(app, app.page.getByText('收件箱暂无匹配邮件', { exact: true }), 'initial Gmail inbox');
  await app.page.getByRole('button', { name: '挂机收信中控' }).click();
  await waitForVisible(app, app.page.getByText('24H 挂机收信守护者', { exact: true }), 'Gmail daemon controls');
  await app.page.getByRole('button', { name: '立即拉取', exact: true }).click();
  await waitForVisible(app, app.page.getByText('同步任务已排队', { exact: true }), 'queued Gmail sync notice');
  await new Promise(resolve => setTimeout(resolve, 250));
  assert.equal(messageRequests, 1, 'a queued sync must not pretend completion by refreshing messages');
  assertPageClean(app);
});

test('Gmail ignores stale mailbox responses and follows nextPageToken', async testContext => {
  const detailStarted = deferred();
  const releaseDetail = deferred();
  const refreshStarted = deferred();
  const releaseRefresh = deferred();
  const mailboxBStarted = deferred();
  const releaseMailboxB = deferred();
  let mailboxARequests = 0;
  let requestedPageToken;
  testContext.after(() => {
    releaseDetail.resolve();
    releaseRefresh.resolve();
    releaseMailboxB.resolve();
  });

  const app = await openApp(testContext, {
    apiRoutes: {
      'GET /api/auth/check': jsonRoute({ success: true, authenticated: true, banned: false }),
      'GET /api/accounts': jsonRoute({ success: true, data: [] }),
      'GET /api/gmail/connections': jsonRoute({ success: true, data: [
        { id: 1, email: 'mailbox-a@example.test' },
        { id: 2, email: 'mailbox-b@example.test' },
      ] }),
      'GET /api/gmail/1/messages': async route => {
        mailboxARequests += 1;
        if (mailboxARequests > 1) {
          refreshStarted.resolve();
          await releaseRefresh.promise;
        }
        await jsonRoute({ success: true, data: { messages: [{
          id: 'message-a', subject: 'Mailbox A marker', from: 'a@example.test', snippet: 'A snippet',
        }], nextPageToken: null } })(route);
      },
      'GET /api/gmail/1/messages/message-a': async route => {
        detailStarted.resolve();
        await releaseDetail.promise;
        await jsonRoute({ success: true, data: {
          id: 'message-a', subject: 'Mailbox A marker', from: 'a@example.test', body: 'Stale A detail body',
        } })(route);
      },
      'GET /api/gmail/2/messages': async (route, request) => {
        requestedPageToken = new URL(request.url()).searchParams.get('pageToken');
        if (requestedPageToken) {
          await jsonRoute({ success: true, data: { messages: [{
            id: 'message-b-2', subject: 'Mailbox B second page', from: 'b@example.test', snippet: 'B2 snippet',
          }], nextPageToken: null } })(route);
          return;
        }
        mailboxBStarted.resolve();
        await releaseMailboxB.promise;
        await jsonRoute({ success: true, data: { messages: [{
          id: 'message-b-1', subject: 'Mailbox B first page', from: 'b@example.test', snippet: 'B1 snippet',
        }], nextPageToken: 'B-PAGE-2' } })(route);
      },
    },
  });

  await app.page.getByRole('button', { name: 'Gmail 收件箱' }).click();
  await waitForVisible(app, app.page.getByText('Mailbox A marker', { exact: true }), 'mailbox A list');
  await app.page.getByText('Mailbox A marker', { exact: true }).click();
  await waitForSignal(detailStarted.promise, 'mailbox A detail request');
  await app.page.getByRole('button', { name: '搜索', exact: true }).click();
  await waitForSignal(refreshStarted.promise, 'slow mailbox A refresh');

  await app.page.getByRole('combobox').first().selectOption('2');
  await waitForSignal(mailboxBStarted.promise, 'mailbox B request');
  assert.equal(await app.page.getByText('Mailbox A marker', { exact: true }).count(), 0);
  await waitForVisible(app, app.page.getByText('选择邮件查看详情', { exact: true }), 'cleared message detail');

  releaseMailboxB.resolve();
  await waitForVisible(app, app.page.getByText('Mailbox B first page', { exact: true }), 'mailbox B list');
  releaseDetail.resolve();
  releaseRefresh.resolve();
  await new Promise(resolve => setTimeout(resolve, 250));
  assert.equal(await app.page.getByText('Mailbox A marker', { exact: true }).count(), 0);
  assert.equal(await app.page.getByText('Stale A detail body', { exact: true }).count(), 0);

  await app.page.getByRole('button', { name: '加载更多', exact: true }).click();
  await waitForVisible(app, app.page.getByText('Mailbox B second page', { exact: true }), 'mailbox B second page');
  assert.equal(requestedPageToken, 'B-PAGE-2');
  assertPageClean(app);
});

test('Gmail read and archive failures stay visible without unhandled errors', async testContext => {
  const app = await openApp(testContext, {
    apiRoutes: {
      'GET /api/auth/check': jsonRoute({ success: true, authenticated: true, banned: false }),
      'GET /api/accounts': jsonRoute({ success: true, data: [] }),
      'GET /api/gmail/connections': jsonRoute({ success: true, data: [
        { id: 1, email: 'mailbox@example.test' },
      ] }),
      'GET /api/gmail/1/messages': jsonRoute({ success: true, data: { messages: [{
        id: 'message-failure', subject: 'Failure action message', from: 'sender@example.test', snippet: 'fixture',
      }], nextPageToken: null } }),
      'PATCH /api/gmail/1/messages/message-failure/read': jsonRoute({
        success: false, message: '标记邮件失败（测试）',
      }, 503),
      'PATCH /api/gmail/1/messages/message-failure/archive': jsonRoute({
        success: false, message: '归档邮件失败（测试）',
      }, 503),
    },
  });

  await app.page.getByRole('button', { name: 'Gmail 收件箱' }).click();
  await waitForVisible(app, app.page.getByText('Failure action message', { exact: true }), 'Gmail message actions');
  await app.page.getByTitle('标记已读').click();
  await waitForVisible(app, app.page.getByText('标记邮件失败（测试）', { exact: true }), 'read failure');
  await app.page.getByTitle('归档').click();
  await waitForVisible(app, app.page.getByText('归档邮件失败（测试）', { exact: true }), 'archive failure');
  assertPageClean(app);
});

test('account library reveals each password through its own control', async testContext => {
  const account = fakeAccount({
    id: 'first-account',
    email: 'first@example.test',
    password: 'first-password',
  });
  const otherAccount = fakeAccount({
    id: 'second-account',
    email: 'second@example.test',
    password: 'second-password',
    soldStatus: 'sold',
  });
  const app = await openApp(testContext, {
    apiRoutes: {
      'GET /api/auth/check': jsonRoute({
        success: true,
        authenticated: true,
        banned: false,
      }),
      'GET /api/accounts': jsonRoute({ success: true, data: [account, otherAccount] }),
    },
  });

  await waitForVisible(
    app,
    app.page.getByText(account.email, { exact: true }),
    'the password fixture account was not rendered',
  );
  await waitForVisible(
    app,
    app.page.getByText(otherAccount.email, { exact: true }),
    'the second password fixture account was not rendered',
  );
  assert.equal(await app.page.getByRole('columnheader', { name: '密码', exact: true }).count(), 1);
  const bodyText = await app.page.locator('body').innerText();
  assert.equal(bodyText.includes(account.password), false);
  assert.equal(bodyText.includes(otherAccount.password), false);

  const revealButton = app.page.getByRole('button', { name: `显示 ${account.email} 的密码` });
  await revealButton.click();
  await waitForVisible(
    app,
    app.page.getByText(account.password, { exact: true }),
    'the account password was not revealed',
  );
  const hideButton = app.page.getByRole('button', { name: `隐藏 ${account.email} 的密码` });
  assert.equal(await hideButton.getAttribute('aria-pressed'), 'true');
  assert.equal(
    await app.page.getByRole('button', { name: `显示 ${otherAccount.email} 的密码` }).count(),
    1,
  );
  assert.equal(
    await app.page.getByRole('button', { name: `隐藏 ${account.email} 的密码` }).count(),
    1,
  );

  await hideButton.click();
  assert.equal(await app.page.getByText(account.password, { exact: true }).count(), 0);
  assert.equal(await revealButton.getAttribute('aria-pressed'), 'false');

  await revealButton.click();
  await app.page.getByRole('button', { name: '已售出 (1)', exact: true }).click();
  await app.page.getByRole('button', { name: '全部 (2)', exact: true }).click();
  await waitForVisible(
    app,
    app.page.getByRole('button', { name: `显示 ${account.email} 的密码` }),
    'the password visibility state was not reset after filtering',
  );
  assertPageClean(app);
});

test('a rejected logout keeps the authenticated account library visible', async testContext => {
  const account = fakeAccount({ email: 'logout-failure@example.test' });
  const app = await openApp(testContext, {
    storage: authenticatedStorage(),
    apiRoutes: {
      'GET /api/auth/check': jsonRoute({
        success: true,
        authenticated: true,
        banned: false,
      }),
      'GET /api/accounts': jsonRoute({ success: true, data: [account] }),
      'POST /api/auth/logout': jsonRoute({
        success: false,
        message: 'Synthetic logout rejection',
      }),
    },
  });

  await waitForVisible(
    app,
    app.page.getByText(account.email, { exact: true }),
    'the logout fixture account was not rendered',
  );
  const logoutResponse = app.page.waitForResponse(
    response => new URL(response.url()).pathname === '/api/auth/logout',
  );
  await app.page.getByTitle('退出登录').click();
  await logoutResponse;
  await waitForVisible(
    app,
    app.page.getByText('退出登录失败，请重试', { exact: true }),
    'the rejected logout was presented as a successful logout',
  );

  assert.equal(
    await app.page.getByRole('heading', { name: '账号库' }).count(),
    1,
    'a rejected logout must keep the authenticated view mounted',
  );
  assertPageClean(app);
});

test('a stale account load cannot overwrite data after re-login', async testContext => {
  const staleAccount = fakeAccount({ id: 'stale-account', email: 'stale@example.test' });
  const freshAccount = fakeAccount({ id: 'fresh-account', email: 'fresh@example.test' });
  const firstRequestStarted = deferred();
  const secondRequestStarted = deferred();
  const releaseFirstRequest = deferred();
  const releaseSecondRequest = deferred();
  let authCheckCount = 0;
  let accountRequestCount = 0;
  const app = await openApp(testContext, {
    storage: authenticatedStorage(),
    apiRoutes: {
      'GET /api/auth/check': async route => {
        authCheckCount += 1;
        await jsonRoute({
          success: true,
          authenticated: authCheckCount === 1,
          banned: false,
        })(route);
      },
      'GET /api/accounts': async route => {
        accountRequestCount += 1;
        if (accountRequestCount === 1) {
          firstRequestStarted.resolve();
          await releaseFirstRequest.promise;
          await jsonRoute({ success: true, data: [staleAccount] })(route);
          return;
        }
        secondRequestStarted.resolve();
        await releaseSecondRequest.promise;
        await jsonRoute({ success: true, data: [freshAccount] })(route);
      },
      'POST /api/auth/logout': jsonRoute({ success: true }),
      'POST /api/auth/login': jsonRoute({ success: true }),
    },
  });

  await waitForSignal(firstRequestStarted.promise, 'initial account request');
  await app.page.getByTitle('退出登录').click();
  await waitForVisible(app, app.page.getByPlaceholder('请输入密码'), 'the login page was not shown after logout');
  await app.page.getByPlaceholder('请输入密码').fill('fixture-password');
  await app.page.getByRole('button', { name: '进入系统', exact: true }).click();
  await waitForSignal(secondRequestStarted.promise, 'account request after re-login');

  releaseSecondRequest.resolve();
  await waitForVisible(app, app.page.getByText(freshAccount.email, { exact: true }), 'fresh account data was not rendered');
  releaseFirstRequest.resolve();
  await new Promise(resolve => setTimeout(resolve, 100));

  assert.equal(await app.page.getByText(freshAccount.email, { exact: true }).count(), 1);
  assert.equal(await app.page.getByText(staleAccount.email, { exact: true }).count(), 0);
  assertPageClean(app);
});

test('out-of-order status PATCH responses preserve both account updates', async testContext => {
  const accountA = fakeAccount({ id: 'account-a', email: 'alpha@example.test' });
  const accountB = fakeAccount({ id: 'account-b', email: 'bravo@example.test' });
  const requests = {
    a: deferred(),
    b: deferred(),
  };
  const releases = {
    a: deferred(),
    b: deferred(),
  };

  const statusHandler = (key, account) => async route => {
    requests[key].resolve();
    await releases[key].promise;
    await jsonRoute({ success: true, data: { ...account, status: 'pro' } })(route);
  };

  const app = await openApp(testContext, {
    storage: authenticatedStorage(),
    apiRoutes: {
      'GET /api/auth/check': jsonRoute({
        success: true,
        authenticated: true,
        banned: false,
      }),
      'GET /api/accounts': jsonRoute({ success: true, data: [accountA, accountB] }),
      'PATCH /api/accounts/account-a/status': statusHandler('a', accountA),
      'PATCH /api/accounts/account-b/status': statusHandler('b', accountB),
    },
  });

  try {
    const rowA = app.page.getByRole('row').filter({ hasText: accountA.email });
    const rowB = app.page.getByRole('row').filter({ hasText: accountB.email });
    await waitForVisible(app, rowA, 'the first fixture account was not rendered');
    await waitForVisible(app, rowB, 'the second fixture account was not rendered');

    await rowA.getByRole('button', { name: '未开启', exact: true }).click();
    await waitForSignal(requests.a.promise, 'the first status PATCH');
    await rowB.getByRole('button', { name: '未开启', exact: true }).click();
    await waitForSignal(requests.b.promise, 'the second status PATCH');

    releases.a.resolve();
    await waitForVisible(
      app,
      rowA.getByRole('button', { name: 'Pro', exact: true }),
      'the first completed status update was not rendered',
    );

    releases.b.resolve();
    await waitForVisible(
      app,
      rowB.getByRole('button', { name: 'Pro', exact: true }),
      'the later status response was not rendered',
    );
    assert.equal(
      await rowA.getByRole('button', { name: 'Pro', exact: true }).count(),
      1,
      'the later response reverted the first completed account update',
    );
    assertPageClean(app);
  } finally {
    releases.a.resolve();
    releases.b.resolve();
  }
});

for (const [terminalStatus, statusLabel] of [
  ['failed', '执行失败'],
  ['cancelled', '已取消'],
]) {
  test(`a ${terminalStatus} Googlemail task with synced results reloads accounts`, async testContext => {
    const staleAccount = fakeAccount({
      id: `googlemail-${terminalStatus}`,
      email: `${terminalStatus}-before@example.test`,
    });
    const refreshedAccount = {
      ...staleAccount,
      email: `${terminalStatus}-after@example.test`,
      secret: 'KRUGS4ZANFZSAYJA',
    };
    let accountLoads = 0;

    const app = await openApp(testContext, {
      storage: authenticatedStorage(),
      apiRoutes: {
        'GET /api/auth/check': jsonRoute({
          success: true,
          authenticated: true,
          banned: false,
        }),
        'GET /api/accounts': async route => {
          accountLoads += 1;
          const account = accountLoads === 1 ? staleAccount : refreshedAccount;
          await jsonRoute({ success: true, data: [account] })(route);
        },
        'GET /api/googlemail/status': jsonRoute({
          success: true,
          data: {
            available: true,
            executionEnabled: true,
            activeTask: null,
            latestTask: null,
          },
        }),
        'GET /api/googlemail/tasks': jsonRoute({ success: true, data: [] }),
        'POST /api/googlemail/tasks': jsonRoute({
          success: true,
          data: {
            taskId: `fixture-${terminalStatus}-task`,
            status: terminalStatus,
            totalCount: 1,
            completedCount: 0,
            failedCount: terminalStatus === 'failed' ? 1 : 0,
            syncedCount: 1,
            manualReviewCount: 0,
            errorCode: terminalStatus === 'failed' ? 'FIXTURE_FAILURE' : null,
          },
        }),
      },
    });

    await waitForVisible(
      app,
      app.page.getByText(staleAccount.email, { exact: true }),
      'the initial Googlemail fixture account was not rendered',
    );
    await app.page.getByRole('button', { name: 'Googlemail', exact: true }).click();
    await waitForVisible(
      app,
      app.page.getByRole('heading', { name: 'Googlemail', exact: true }),
      'the Googlemail view was not rendered',
    );
    await waitForVisible(
      app,
      app.page.getByRole('checkbox', { name: `选择 ${staleAccount.email}` }),
      'the Googlemail account selector was not rendered',
    );

    await app.page.getByRole('checkbox', { name: `选择 ${staleAccount.email}` }).check();
    app.page.once('dialog', dialog => dialog.accept());
    await app.page.getByRole('button', { name: '启动任务', exact: true }).click();
    await waitForVisible(
      app,
      app.page.getByText(statusLabel, { exact: true }),
      `the ${terminalStatus} terminal task state was not rendered`,
    );
    await waitForVisible(
      app,
      app.page.getByText(refreshedAccount.email, { exact: true }),
      `the ${terminalStatus} task synced data but did not reload the account list`,
    );

    assert.equal(accountLoads, 2, 'the account API should be loaded once after synced results');
    assertPageClean(app);
  });
}

test('Googlemail blocks empty numeric options before starting a task', async testContext => {
  const account = fakeAccount({ id: 'numeric-option-account' });
  let taskStartCount = 0;
  const app = await openApp(testContext, {
    storage: authenticatedStorage(),
    apiRoutes: {
      'GET /api/auth/check': jsonRoute({ success: true, authenticated: true, banned: false }),
      'GET /api/accounts': jsonRoute({ success: true, data: [account] }),
      'GET /api/googlemail/status': jsonRoute({
        success: true,
        data: {
          available: true,
          executionEnabled: true,
          activeTask: null,
          latestTask: null,
        },
      }),
      'GET /api/googlemail/tasks': jsonRoute({ success: true, data: [] }),
      'POST /api/googlemail/tasks': async route => {
        taskStartCount += 1;
        await jsonRoute({ success: true, data: { taskId: 'unexpected-task', status: 'running' } })(route);
      },
    },
  });

  await waitForVisible(app, app.page.getByRole('button', { name: 'Googlemail', exact: true }), 'Googlemail navigation was not rendered');
  await app.page.getByRole('button', { name: 'Googlemail', exact: true }).click();
  await waitForVisible(app, app.page.getByRole('heading', { name: 'Googlemail', exact: true }), 'Googlemail view was not rendered');
  await app.page.getByRole('checkbox', { name: `选择 ${account.email}` }).check();
  await app.page.locator('input[type="number"]').first().fill('');
  await app.page.getByRole('button', { name: '启动任务', exact: true }).click();

  await waitForVisible(app, app.page.getByText(/操作延迟必须在/), 'invalid numeric option feedback was not shown');
  assert.equal(taskStartCount, 0, 'invalid numeric options must not start a task');
  assertPageClean(app);
});

test('Googlemail polling does not overlap slow status requests', async testContext => {
  const account = fakeAccount({ id: 'polling-account' });
  const firstStatusRequest = deferred();
  const releaseStatusRequest = deferred();
  let statusRequestCount = 0;
  let accountLoadCount = 0;
  const app = await openApp(testContext, {
    storage: authenticatedStorage(),
    apiRoutes: {
      'GET /api/auth/check': jsonRoute({ success: true, authenticated: true, banned: false }),
      'GET /api/accounts': async route => {
        accountLoadCount += 1;
        await jsonRoute({ success: true, data: [account] })(route);
      },
      'GET /api/googlemail/status': jsonRoute({
        success: true,
        data: {
          available: true,
          executionEnabled: true,
          activeTask: null,
          latestTask: null,
        },
      }),
      'GET /api/googlemail/tasks': jsonRoute({ success: true, data: [] }),
      'POST /api/googlemail/tasks': jsonRoute({
        success: true,
        data: {
          taskId: 'polling-task',
          status: 'running',
          totalCount: 1,
          completedCount: 0,
          failedCount: 0,
        },
      }),
      'GET /api/googlemail/tasks/polling-task': async route => {
        statusRequestCount += 1;
        if (statusRequestCount === 1) firstStatusRequest.resolve();
        await releaseStatusRequest.promise;
        await jsonRoute({
          success: true,
          data: {
            taskId: 'polling-task',
            status: 'completed',
            totalCount: 1,
            completedCount: 1,
            failedCount: 0,
            syncedCount: 0,
            manualReviewCount: 0,
          },
        })(route);
      },
    },
  });

  await app.page.getByRole('button', { name: 'Googlemail', exact: true }).click();
  await waitForVisible(app, app.page.getByRole('heading', { name: 'Googlemail', exact: true }), 'Googlemail view was not rendered');
  await app.page.getByRole('checkbox', { name: `选择 ${account.email}` }).check();
  app.page.once('dialog', dialog => dialog.accept());
  await app.page.getByRole('button', { name: '启动任务', exact: true }).click();
  await waitForSignal(firstStatusRequest.promise, 'first task status request');
  await new Promise(resolve => setTimeout(resolve, 1800));
  assert.equal(statusRequestCount, 1, 'a second status request must wait for the first one');

  releaseStatusRequest.resolve();
  await waitForVisible(app, app.page.getByText('已完成', { exact: true }), 'the released task status was not rendered');
  assert.equal(accountLoadCount, 2, 'completed tasks should refresh the account list once');
  assertPageClean(app);
});

test('an older history request cannot overwrite the newly selected account', async testContext => {
  const accountA = fakeAccount({ id: 'history-a', email: 'history-a@example.test' });
  const accountB = fakeAccount({ id: 'history-b', email: 'history-b@example.test' });
  const requests = {
    a: deferred(),
    b: deferred(),
  };
  const releases = {
    a: deferred(),
    b: deferred(),
  };
  const historyA = [{
    id: 'history-record-a',
    fieldName: 'password',
    oldValue: 'old-a-marker',
    newValue: 'new-a-marker',
    changedAt: '2026-09-14 10:00:00',
  }];
  const historyB = [{
    id: 'history-record-b',
    fieldName: 'password',
    oldValue: 'old-b-marker',
    newValue: 'new-b-marker',
    changedAt: '2026-09-14 11:00:00',
  }];

  const historyHandler = (key, history) => async route => {
    requests[key].resolve();
    await releases[key].promise;
    await jsonRoute({ success: true, data: history })(route);
  };

  const app = await openApp(testContext, {
    storage: authenticatedStorage(),
    apiRoutes: {
      'GET /api/auth/check': jsonRoute({
        success: true,
        authenticated: true,
        banned: false,
      }),
      'GET /api/accounts': jsonRoute({ success: true, data: [accountA, accountB] }),
      'GET /api/accounts/history-a/history': historyHandler('a', historyA),
      'GET /api/accounts/history-b/history': historyHandler('b', historyB),
    },
  });

  try {
    const rowA = app.page.getByRole('row').filter({ hasText: accountA.email });
    const rowB = app.page.getByRole('row').filter({ hasText: accountB.email });
    await waitForVisible(app, rowA, 'the first history fixture account was not rendered');

    await rowA.getByTitle('查看修改历史').click();
    await waitForSignal(requests.a.promise, 'the first account history request');
    const drawerHeading = app.page.getByRole('heading', { name: '修改历史' });
    await waitForVisible(app, drawerHeading, 'the history drawer did not open');
    await drawerHeading.locator('xpath=../../..').getByRole('button').click();
    await drawerHeading.waitFor({ state: 'hidden' });

    await rowB.getByTitle('查看修改历史').click();
    await waitForSignal(requests.b.promise, 'the second account history request');
    releases.b.resolve();
    await waitForVisible(
      app,
      app.page.getByText('new-b-marker', { exact: true }),
      'the newly selected account history did not render',
    );

    const oldResponse = app.page.waitForResponse(
      response => new URL(response.url()).pathname === '/api/accounts/history-a/history',
    );
    releases.a.resolve();
    const response = await oldResponse;
    await response.finished();
    await app.page.evaluate(() => new Promise(resolveFrame => {
      requestAnimationFrame(() => requestAnimationFrame(resolveFrame));
    }));

    assert.equal(
      await app.page.getByText('new-b-marker', { exact: true }).count(),
      1,
      'the selected account history was replaced by an older response',
    );
    assert.equal(
      await app.page.getByText('new-a-marker', { exact: true }).count(),
      0,
      'the stale history response leaked into the active drawer',
    );
    assertPageClean(app);
  } finally {
    releases.a.resolve();
    releases.b.resolve();
  }
});
