import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { readFile } from 'node:fs/promises';
import { dirname, extname, resolve, sep } from 'node:path';
import test, { after, before } from 'node:test';
import { fileURLToPath } from 'node:url';

import { chromium } from '../googlemail/node_modules/playwright/index.mjs';

const repositoryRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const staticRoot = resolve(repositoryRoot, 'static');
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

async function openApp(t, { storage = {}, apiRoutes = {} } = {}) {
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

  await page.route('**/api/**', async route => {
    const request = route.request();
    const pathname = new URL(request.url()).pathname;
    const routeKey = `${request.method()} ${pathname}`;
    const handler = apiRoutes[routeKey];

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
  await page.goto(baseUrl, { waitUntil: 'domcontentloaded' });
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
      const relativePath = pathname === '/' ? 'index.html' : pathname.slice(1);
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
