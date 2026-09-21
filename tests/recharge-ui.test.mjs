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
const contentTypes = {
  '.css': 'text/css; charset=utf-8',
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
};

let browser;
let server;
let baseUrl;

function jsonRoute(payload, status = 200) {
  return route => route.fulfill({
    status,
    contentType: 'application/json',
    body: JSON.stringify(payload),
  });
}

async function launchHeadlessBrowser() {
  if (process.env.GOOGLE_MANAGER_CHROME_EXECUTABLE_PATH || process.env.FLASK_ENV === 'production') {
    return chromium.launch(getBrowserLaunchOptions({ headless: true }));
  }

  const candidates = [
    {},
    { channel: 'chrome' },
    { channel: 'msedge' },
  ];
  const failures = [];
  for (const options of candidates) {
    try {
      return await chromium.launch({ ...options, headless: true });
    } catch (error) {
      failures.push(error);
    }
  }
  throw new AggregateError(failures, 'No Playwright-compatible browser is available');
}

async function openRecharge(testContext, routes = {}, mode = 'mock', options = {}) {
  const context = await browser.newContext();
  testContext.after(() => context.close());
  const page = await context.newPage();
  if (options.pollDelayMs) {
    await page.addInitScript(pollDelayMs => {
      const nativeSetTimeout = window.setTimeout.bind(window);
      window.setTimeout = (callback, delay, ...args) => nativeSetTimeout(
        callback,
        delay === 5000 ? pollDelayMs : delay,
        ...args,
      );
    }, options.pollDelayMs);
  }
  const unexpectedRequests = [];
  const apiRoutes = {
    'GET /api/recharge/config': jsonRoute({
      success: true,
      data: { mode, enabled: true, features: {} },
    }),
    'GET /api/recharge/stats/avg-processing-time': jsonRoute({ success: true, data: [] }),
    'GET /api/recharge/agreement': jsonRoute({
      success: true,
      data: { content: 'Synthetic agreement' },
    }),
    ...routes,
  };

  await page.route('**/api/**', async route => {
    const request = route.request();
    const pathname = new URL(request.url()).pathname;
    const routeKey = `${request.method()} ${pathname}`;
    const handler = apiRoutes[routeKey];
    if (!handler) {
      unexpectedRequests.push(routeKey);
      await jsonRoute({ success: false, message: `Unexpected request: ${routeKey}` }, 500)(route);
      return;
    }
    await handler(route, request);
  });

  await page.goto(`${baseUrl}/recharge`, { waitUntil: 'domcontentloaded' });
  await page.getByRole('heading', { name: '自助充值与订单服务' }).waitFor();
  return { page, unexpectedRequests };
}

before(async () => {
  server = createServer(async (request, response) => {
    try {
      if (request.method !== 'GET') {
        response.writeHead(405).end();
        return;
      }
      const pathname = decodeURIComponent(new URL(request.url, 'http://localhost').pathname);
      const relativePath = pathname === '/' || pathname === '/recharge'
        ? 'index.html'
        : pathname.slice(1);
      const filePath = resolve(staticRoot, relativePath);
      if (!filePath.startsWith(`${staticRoot}${sep}`)) {
        response.writeHead(403).end();
        return;
      }
      const body = await readFile(filePath);
      response.writeHead(200, {
        'cache-control': 'no-store',
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

test('live recharge does not present synthetic processing times as recent statistics', async testContext => {
  let statsRequests = 0;
  const app = await openRecharge(testContext, {
    'GET /api/recharge/stats/avg-processing-time': async route => {
      statsRequests += 1;
      await jsonRoute({
        success: true,
        data: [{ plan_type: 'PLUS', plan_name: 'Synthetic plan', avg_seconds: 60 }],
      })(route);
    },
  }, 'live');

  await app.page.waitForTimeout(100);
  assert.equal(statsRequests, 0);
  assert.equal(await app.page.getByText(/近 7 天平均耗时/).count(), 0);
  assert.deepEqual(app.unexpectedRequests, []);
});

test('a failed revalidation clears the previous CDK result', async testContext => {
  let validationRequests = 0;
  const app = await openRecharge(testContext, {
    'POST /api/recharge/redeem-codes/validate': async route => {
      validationRequests += 1;
      if (validationRequests === 1) {
        await jsonRoute({ success: true, data: {
          plan_type: 'PLUS',
          plan_name: 'Synthetic Plus',
          is_renewal_supported: true,
        } })(route);
        return;
      }
      await jsonRoute({ success: false, message: '卡密重新校验失败' }, 400)(route);
    },
  });

  const input = app.page.getByPlaceholder(/CDK 卡密/);
  assert.match(await input.getAttribute('placeholder'), /4-120/);
  await input.fill('PLUS-REVALIDATE-001');
  await app.page.getByRole('button', { name: '验证卡密' }).click();
  await app.page.getByText('卡密已核验有效').waitFor();
  await app.page.getByRole('button', { name: '验证卡密' }).click();
  await app.page.getByText('卡密重新校验失败').waitFor();

  assert.equal(await app.page.getByText('卡密已核验有效').count(), 0);
  assert.deepEqual(app.unexpectedRequests, []);
});

test('batch lookup deduplicates codes before applying the 50-code limit', async testContext => {
  const requestBodies = [];
  const app = await openRecharge(testContext, {
    'POST /api/recharge/tasks/lookup-batch': async (route, request) => {
      requestBodies.push(request.postDataJSON());
      await jsonRoute({ success: true, data: [] })(route);
    },
  });

  await app.page.getByRole('button', { name: '进度查询' }).click();
  await app.page.getByRole('button', { name: /批量查询 \(最多50个\)/ }).click();
  await app.page.getByPlaceholder(/每行输入一个卡密/).fill(
    Array.from({ length: 51 }, () => 'PLUS-DUPLICATE-001').join('\n'),
  );
  await app.page.getByText('已输入 1 / 50 个卡密').waitFor();
  await app.page.getByRole('button', { name: '批量查询进度' }).click();
  await app.page.waitForTimeout(100);

  assert.deepEqual(requestBodies, [{ redeem_codes: ['PLUS-DUPLICATE-001'] }]);
  assert.deepEqual(app.unexpectedRequests, []);
});

test('a failed billing refresh removes the stale subscription result', async testContext => {
  let billingRequests = 0;
  const app = await openRecharge(testContext, {
    'POST /api/recharge/billing/query': async route => {
      billingRequests += 1;
      if (billingRequests === 1) {
        await jsonRoute({ success: true, data: {
          plan_name: 'Synthetic subscription',
          status: 'active',
          auto_renew: true,
          card_brand: 'Visa',
          card_last4: '1234',
          next_billing_date: '2026-10-01',
          invoices: [],
        } })(route);
        return;
      }
      await jsonRoute({ success: false, message: '账单凭证已失效' }, 401)(route);
    },
  });

  await app.page.getByRole('button', { name: '账单续费' }).click();
  await app.page.getByPlaceholder(/accessToken/).fill('synthetic-billing-token');
  await app.page.getByRole('button', { name: '查询账单状态' }).click();
  await app.page.getByText('Synthetic subscription').waitFor();
  await app.page.getByRole('button', { name: '查询账单状态' }).click();
  await app.page.getByText('账单凭证已失效').waitFor();

  assert.equal(await app.page.getByText('Synthetic subscription').count(), 0);
  assert.deepEqual(app.unexpectedRequests, []);
});

test('manual refresh of the same active task restarts polling', async testContext => {
  let taskRequests = 0;
  const app = await openRecharge(testContext, {
    'GET /api/recharge/tasks/TK-SAME-STATUS': async route => {
      taskRequests += 1;
      const completed = taskRequests >= 3;
      await jsonRoute({ success: true, data: {
        task_no: 'TK-SAME-STATUS',
        plan_type: 'PLUS',
        status: completed ? 'completed' : 'processing',
        status_text: completed ? '充值完成' : '任务处理中',
        is_mock: true,
      } })(route);
    },
  }, 'mock', { pollDelayMs: 500 });

  await app.page.getByRole('button', { name: '进度查询' }).click();
  await app.page.getByPlaceholder(/CDK 卡密或任务编号/).fill('TK-SAME-STATUS');
  await app.page.getByRole('button', { name: '立即查询' }).click();
  await app.page.getByText('任务处理中', { exact: true }).waitFor();
  await app.page.getByRole('button', { name: '立即查询' }).click();
  await app.page.getByText('充值完成', { exact: true }).waitFor({ timeout: 3_000 });

  assert.equal(taskRequests, 3);
  assert.deepEqual(app.unexpectedRequests, []);
});

test('editing the CDK clears the previous task success summary', async testContext => {
  const app = await openRecharge(testContext, {
    'POST /api/recharge/redeem-codes/validate': jsonRoute({
      success: true, data: { plan_type: 'PLUS' },
    }),
    'POST /api/recharge/submission-challenges': jsonRoute({
      success: true, data: { challenge_token: 'synthetic-challenge' },
    }),
    'POST /api/recharge/tasks': jsonRoute({
      success: true, data: { task_no: 'TK-PREVIOUS-TASK', plan_type: 'PLUS', status: 'processing' },
    }, 201),
  });
  const cdk = app.page.getByPlaceholder(/CDK 卡密（如/);
  await cdk.fill('PLUS-PREVIOUS-CDK');
  await app.page.getByRole('button', { name: '验证卡密', exact: true }).click();
  await app.page.getByText('卡密已核验有效').waitFor();
  await app.page.getByPlaceholder(/粘贴来自 chatgpt/).fill('synthetic-credential');
  await app.page.getByPlaceholder('user@gmail.com', { exact: true }).fill('fixture@example.test');
  await app.page.getByRole('checkbox', { name: /我已仔细核对/ }).check();
  await app.page.getByRole('checkbox', { name: /我已完整阅读/ }).check();
  await app.page.getByRole('button', { name: '同意协议并提交充值任务' }).click();
  await app.page.getByText('任务提交成功！', { exact: true }).waitFor();
  await cdk.fill('PLUS-NEXT-CDK');
  assert.equal(await app.page.getByText('任务提交成功！', { exact: true }).count(), 0);
  assert.deepEqual(app.unexpectedRequests, []);
});

test('batch lookup enforces unique-code and length boundaries before sending', async testContext => {
  const requests = [];
  const app = await openRecharge(testContext, {
    'POST /api/recharge/tasks/lookup-batch': async (route, request) => {
      requests.push(request.postDataJSON());
      await jsonRoute({ success: true, data: [] })(route);
    },
  });
  await app.page.getByRole('button', { name: '进度查询' }).click();
  await app.page.getByRole('button', { name: /批量查询 \(最多50个\)/ }).click();
  const input = app.page.getByPlaceholder(/每行输入一个卡密/);
  const submit = app.page.getByRole('button', { name: '批量查询进度' });
  for (const invalid of ['abc', 'X'.repeat(121)]) {
    await input.fill(invalid);
    await submit.click();
    await app.page.getByText('每个 CDK 卡密长度须在 4-120 字符之间').waitFor();
  }
  const codes = Array.from({ length: 51 }, (_, index) => `PLUS-BOUNDARY-${index}`);
  await input.fill(codes.join('\n'));
  await submit.click();
  await app.page.getByText('单次最多支持查询 50 个卡密').waitFor();
  assert.equal(requests.length, 0);
  const allowed = ['ABCD', 'X'.repeat(120), ...codes.slice(0, 48)];
  await input.fill(allowed.join('\n'));
  const response = app.page.waitForResponse('**/api/recharge/tasks/lookup-batch');
  await submit.click();
  await response;
  assert.deepEqual(requests, [{ redeem_codes: allowed }]);
  assert.deepEqual(app.unexpectedRequests, []);
});

test('malformed batch responses show an error without crashing the page', async testContext => {
  const app = await openRecharge(testContext, {
    'POST /api/recharge/tasks/lookup-batch': jsonRoute({ success: true, data: {} }),
  });
  const pageErrors = [];
  app.page.on('pageerror', error => pageErrors.push(error.message));
  await app.page.getByRole('button', { name: '进度查询' }).click();
  await app.page.getByRole('button', { name: /批量查询 \(最多50个\)/ }).click();
  await app.page.getByPlaceholder(/每行输入一个卡密/).fill('PLUS-MALFORMED');
  await app.page.getByRole('button', { name: '批量查询进度' }).click();
  await app.page.getByText('批量查询失败', { exact: true }).waitFor();
  assert.deepEqual(pageErrors, []);
  assert.deepEqual(app.unexpectedRequests, []);
});
