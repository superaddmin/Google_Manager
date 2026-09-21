import assert from 'node:assert/strict';
import { existsSync } from 'node:fs';
import { mkdir, mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { createInterface } from 'node:readline';
import { spawn } from 'node:child_process';
import test, { after, before } from 'node:test';
import { fileURLToPath } from 'node:url';

import { chromium } from '../googlemail/node_modules/playwright/index.mjs';
import { getBrowserLaunchOptions } from '../googlemail/src/browser-runtime.mjs';

const repositoryRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const fixtureScript = resolve(repositoryRoot, 'tests', 'recharge_flow_server.py');
const artifactDirectory = resolve(repositoryRoot, '.test-tmp');
const syntheticCdk = 'PLUS-SYNTHETIC-E2E-001';
const syntheticCredential = 'synthetic-session-token-for-browser-test';
const syntheticEmail = 'recharge-flow@example.test';

let browser;
let fixture;
let temporaryDirectory;

async function terminateChild(child) {
  if (!child || child.exitCode !== null || child.signalCode !== null) return;
  const closed = new Promise(resolveClose => child.once('close', resolveClose));
  child.kill();
  await Promise.race([
    closed,
    new Promise(resolveTimeout => setTimeout(resolveTimeout, 2_000)),
  ]);
}

function pythonExecutable() {
  const configured = process.env.PYTHON_EXECUTABLE || process.env.PYTHON;
  if (configured) return configured;
  const localCandidates = process.platform === 'win32'
    ? [resolve(repositoryRoot, '.venv', 'Scripts', 'python.exe')]
    : [resolve(repositoryRoot, '.venv', 'bin', 'python')];
  return localCandidates.find(candidate => existsSync(candidate)) ||
    (process.platform === 'win32' ? 'python' : 'python3');
}

async function startFixture() {
  temporaryDirectory = await mkdtemp(join(tmpdir(), 'google-manager-recharge-flow-'));
  const databasePath = resolve(temporaryDirectory, 'recharge-flow.sqlite');
  const child = spawn(pythonExecutable(), [
    fixtureScript,
    '--database', databasePath,
  ], {
    cwd: repositoryRoot,
    env: { ...process.env, PYTHONUNBUFFERED: '1' },
    stdio: ['ignore', 'pipe', 'pipe'],
    windowsHide: true,
  });

  let stderr = '';
  child.stderr.setEncoding('utf8');
  child.stderr.on('data', chunk => {
    stderr += chunk;
  });

  const output = createInterface({ input: child.stdout });
  let startup;
  try {
    startup = await new Promise((resolveStartup, rejectStartup) => {
      const timer = setTimeout(() => {
        rejectStartup(new Error(`Timed out starting recharge fixture: ${stderr}`));
      }, 15_000);
      const fail = error => {
        clearTimeout(timer);
        rejectStartup(new Error(
          `Recharge fixture exited before startup (${error?.message || error}): ${stderr}`,
        ));
      };
      child.once('error', fail);
      child.once('exit', code => fail(new Error(`exit code ${code}`)));
      output.once('line', line => {
        clearTimeout(timer);
        child.off('error', fail);
        child.removeAllListeners('exit');
        try {
          resolveStartup(JSON.parse(line));
        } catch (error) {
          rejectStartup(new Error(`Invalid recharge fixture startup message: ${line}`, { cause: error }));
        }
      });
    });
  } catch (error) {
    await terminateChild(child);
    await rm(temporaryDirectory, { recursive: true, force: true });
    temporaryDirectory = undefined;
    throw error;
  } finally {
    output.close();
  }

  return {
    baseUrl: startup.base_url,
    child,
    databasePath,
    stderr: () => stderr,
  };
}

async function launchHeadlessBrowser() {
  if (process.env.GOOGLE_MANAGER_CHROME_EXECUTABLE_PATH || process.env.FLASK_ENV === 'production') {
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

async function stopFixture() {
  if (!fixture?.child) return;
  try {
    await fetch(`${fixture.baseUrl}/__test__/shutdown`, {
      method: 'POST', signal: AbortSignal.timeout(5_000),
    });
  } catch {
    // The process may have already exited after a failed startup or test.
  }

  if (fixture.child.exitCode === null && fixture.child.signalCode === null) {
    await Promise.race([
      new Promise(resolveExit => fixture.child.once('exit', resolveExit)),
      new Promise(resolveTimeout => setTimeout(resolveTimeout, 5_000)),
    ]);
  }
  if (fixture.child.exitCode === null && fixture.child.signalCode === null) {
    await terminateChild(fixture.child);
  }
}

async function fixtureState() {
  const response = await fetch(`${fixture.baseUrl}/__test__/state`, {
    signal: AbortSignal.timeout(5_000),
  });
  assert.equal(response.status, 200);
  return response.json();
}

async function assertLayoutFitsViewport(page, coreButton, expectedWidth, label) {
  const dimensions = await page.evaluate(() => ({
    clientWidth: document.documentElement.clientWidth,
    scrollWidth: document.documentElement.scrollWidth,
  }));
  assert.ok(
    dimensions.scrollWidth <= dimensions.clientWidth + 1,
    `${label} layout overflows horizontally: ${JSON.stringify(dimensions)}`,
  );

  await coreButton.scrollIntoViewIfNeeded();
  assert.equal(await coreButton.isVisible(), true, `${label} core action must be visible`);
  const box = await coreButton.boundingBox();
  assert.ok(box, `${label} core action must have a layout box`);
  assert.ok(
    box.x >= -1 && box.x + box.width <= expectedWidth + 1,
    `${label} core action exceeds viewport: ${JSON.stringify(box)}`,
  );
}

before(async () => {
  await mkdir(artifactDirectory, { recursive: true });
  fixture = await startFixture();
  browser = await launchHeadlessBrowser();
});

after(async () => {
  try {
    await browser?.close();
  } finally {
    try {
      await stopFixture();
    } finally {
      if (temporaryDirectory) {
        await rm(temporaryDirectory, { recursive: true, force: true });
      }
    }
  }
});

test('recharge completes through browser, Flask, isolated upstream, and SQLite', async testContext => {
  const context = await browser.newContext({ viewport: { width: 1280, height: 900 } });
  testContext.after(() => context.close());
  const page = await context.newPage();
  const pageErrors = [];
  const rechargeResponses = [];

  page.on('pageerror', error => pageErrors.push(`${error.name}: ${error.message}`));
  page.on('response', response => {
    const url = new URL(response.url());
    if (url.pathname.startsWith('/api/recharge/')) {
      rechargeResponses.push({
        method: response.request().method(),
        path: url.pathname,
        status: response.status(),
      });
    }
  });

  await page.goto(`${fixture.baseUrl}/recharge`, { waitUntil: 'domcontentloaded' });
  await page.getByRole('heading', { name: '自助充值与订单服务' }).waitFor();
  await assertLayoutFitsViewport(
    page,
    page.getByRole('button', { name: '验证卡密', exact: true }),
    1280,
    'desktop recharge',
  );

  await page.getByPlaceholder(/CDK 卡密（如/).fill(syntheticCdk);
  await page.getByRole('button', { name: '验证卡密', exact: true }).click();
  await page.getByText('Synthetic ChatGPT Plus', { exact: true }).waitFor();

  await page.getByPlaceholder(/粘贴来自 chatgpt\.com\/api\/auth\/session/).fill(syntheticCredential);
  await page.getByPlaceholder('user@gmail.com').fill(syntheticEmail);
  await page.locator('label').filter({ hasText: '我已仔细核对充值邮箱为' })
    .getByRole('checkbox').check();
  await page.locator('label').filter({ hasText: '我已完整阅读并同意' })
    .getByRole('checkbox').check();

  await page.getByRole('button', { name: '同意协议并提交充值任务', exact: true }).click();
  const successCard = page.getByText('任务提交成功！', { exact: true }).locator('xpath=..');
  await successCard.waitFor();
  const taskNumber = (await successCard.locator('xpath=..').innerText())
    .match(/TK-LIVE-[A-Z0-9-]+/)?.[0];
  assert.ok(taskNumber, 'the UI must render the Flask-created task number');

  assert.deepEqual(
    rechargeResponses.filter(item => item.method === 'POST').slice(-3),
    [
      { method: 'POST', path: '/api/recharge/redeem-codes/validate', status: 200 },
      { method: 'POST', path: '/api/recharge/submission-challenges', status: 200 },
      { method: 'POST', path: '/api/recharge/tasks', status: 201 },
    ],
  );

  await page.getByRole('button', { name: '查看履约进度', exact: true }).click();
  await page.getByRole('heading', { name: '任务与卡密进度工作台' }).waitFor();
  await page.getByText('任务处理中', { exact: true }).waitFor();
  await page.getByText('已完成', { exact: true }).waitFor({ timeout: 12_000 });

  await page.reload({ waitUntil: 'domcontentloaded' });
  await page.getByRole('heading', { name: '自助充值与订单服务' }).waitFor();
  await page.getByRole('button', { name: '进度查询', exact: true }).click();
  await page.getByPlaceholder(/输入 CDK 卡密或任务编号/).fill(taskNumber);
  await page.getByRole('button', { name: '立即查询', exact: true }).click();
  await page.getByText('已完成', { exact: true }).waitFor();
  assert.equal(await page.getByText(`任务编号：${taskNumber}`, { exact: true }).count(), 1);
  await assertLayoutFitsViewport(
    page,
    page.getByRole('button', { name: '立即查询', exact: true }),
    1280,
    'desktop completed task',
  );
  await page.screenshot({
    path: resolve(artifactDirectory, 'recharge-flow-desktop.png'),
    fullPage: true,
    animations: 'disabled',
  });

  const state = await fixtureState();
  assert.deepEqual(state.tasks, [{ task_no: taskNumber, status: 'completed', is_mock: false }]);
  assert.equal(state.upstream.create_count, 1, 'the browser flow must create one upstream task');
  assert.ok(state.upstream.validation_count >= 2, 'challenge creation must revalidate the CDK upstream');
  assert.ok(state.upstream.status_read_count >= 3, 'polling and refresh lookup must reach the upstream');
  assert.equal(state.upstream.challenge_token_forwarded, false);
  assert.equal(state.upstream.idempotency_matches_client_task, true);
  assert.deepEqual(pageErrors, [], `page errors: ${pageErrors.join(' | ')}`);
  assert.equal(
    rechargeResponses.some(item => item.status >= 400),
    false,
    `unexpected recharge API failure: ${JSON.stringify(rechargeResponses)}`,
  );
});

test('recharge entry fits a 375px mobile viewport', async testContext => {
  const context = await browser.newContext({ viewport: { width: 375, height: 812 } });
  testContext.after(() => context.close());
  const page = await context.newPage();
  const pageErrors = [];
  page.on('pageerror', error => pageErrors.push(`${error.name}: ${error.message}`));

  await page.goto(`${fixture.baseUrl}/recharge`, { waitUntil: 'domcontentloaded' });
  await page.getByRole('heading', { name: '自助充值与订单服务' }).waitFor();
  await assertLayoutFitsViewport(
    page,
    page.getByRole('button', { name: '验证卡密', exact: true }),
    375,
    '375px mobile recharge',
  );
  await page.screenshot({
    path: resolve(artifactDirectory, 'recharge-flow-mobile.png'),
    fullPage: true,
    animations: 'disabled',
  });
  assert.deepEqual(pageErrors, [], `mobile page errors: ${pageErrors.join(' | ')}`);
});
