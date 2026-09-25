import assert from 'node:assert/strict';
import { existsSync } from 'node:fs';
import { mkdtemp, readFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { spawn } from 'node:child_process';
import { createInterface } from 'node:readline';
import test from 'node:test';
import { chromium } from '../googlemail/node_modules/playwright/index.mjs';
import { getBrowserLaunchOptions } from '../googlemail/src/browser-runtime.mjs';

test('CDK browser: inventory → batch → independent approval → export → redemption → reconciliation', { timeout: 120000 }, async () => {
    const directory = await mkdtemp(join(tmpdir(), 'google-manager-cdk-flow-'));
    const localPython = resolve(process.platform === 'win32' ? '.venv/Scripts/python.exe' : '.venv/bin/python');
    const executable = process.env.PYTHON_EXECUTABLE || (existsSync(localPython) ? localPython : 'python');
    const fixture = spawn(executable, ['tests/cdk_flow_server.py', '--database', join(directory, 'fixture.sqlite')], {
        cwd: resolve('.'), env: { ...process.env, PYTHONUNBUFFERED: '1', PYTHONIOENCODING: 'utf-8' }, windowsHide: true, stdio: ['ignore', 'pipe', 'pipe']
    });
    let stderr = '';
    fixture.stderr.on('data', chunk => { stderr += chunk; });
    let browser;
    let baseUrl;
    try {
        const output = createInterface({ input: fixture.stdout });
        const startup = await new Promise((resolveStartup, rejectStartup) => {
            const timer = setTimeout(() => rejectStartup(new Error('CDK fixture startup timed out: ' + stderr)), 15000);
            fixture.once('exit', code => { clearTimeout(timer); rejectStartup(new Error('CDK fixture exited: ' + code + ': ' + stderr)); });
            output.once('line', line => { clearTimeout(timer); resolveStartup(JSON.parse(line)); });
        });
        output.close();
        baseUrl = startup.base_url;
        if (process.env.GOOGLE_MANAGER_CHROME_EXECUTABLE_PATH || process.env.FLASK_ENV === 'production') {
            browser = await chromium.launch(getBrowserLaunchOptions({ headless: true }));
        } else {
            for (const options of [{}, { channel: 'chrome' }, { channel: 'msedge' }]) {
                try { browser = await chromium.launch({ ...options, headless: true }); break; } catch {}
            }
            assert.ok(browser, 'A Playwright-compatible browser is required');
        }
        const maker = await browser.newPage({ acceptDownloads: true });
        const checker = await browser.newPage();
        const customer = await browser.newPage({ viewport: { width: 390, height: 844 } });
        const errors = [];
        for (const page of [maker, checker, customer]) page.on('pageerror', error => errors.push(error.message));
        async function login(page, username) {
            await page.goto(baseUrl + '/admin');
            await page.getByLabel('用户名', { exact: true }).fill(username);
            await page.getByLabel('密码', { exact: true }).fill(username + '-browser-fixture-password');
            await page.getByRole('button', { name: '登录卡密工作台' }).click();
            await page.getByRole('navigation', { name: '卡密管理导航' }).waitFor();
        }
        async function formSubmit(page, fields) {
            const dialog = page.getByRole('dialog');
            for (const [label, value] of Object.entries(fields)) {
                const field = dialog.getByLabel(label, { exact: true });
                if (typeof value === 'object') await field.selectOption(value);
                else await field.fill(String(value));
            }
            await dialog.getByRole('button', { name: '确认提交' }).click();
            await dialog.waitFor({ state: 'hidden' });
        }
        async function selectTab(page, label) {
            await page.getByRole('navigation', { name: '卡密管理导航' }).getByRole('button', { name: label, exact: true }).click();
        }
        await login(maker, 'maker');
        await maker.getByRole('region', { name: 'CDK 生成流程' }).waitFor();
        assert.equal(await maker.getByRole('navigation', { name: '充值后台模块' }).getByRole('link', { name: 'CDK 卡密管理' }).getAttribute('aria-current'), 'page');
        await selectTab(maker, '渠道');
        await maker.getByRole('button', { name: '新建渠道' }).click();
        await formSubmit(maker, { '渠道编号': 'browser-channel', '渠道名称': '浏览器验收渠道' });
        await selectTab(maker, '权益');
        await maker.getByRole('button', { name: '新增权益版本' }).click();
        await formSubmit(maker, { '产品编号': 'browser-plus', '权益名称': '浏览器 Plus 权益', '套餐': { label: 'PLUS' } });
        await selectTab(maker, '供应商库存');
        await maker.getByRole('button', { name: '导入库存' }).click();
        const until = new Date(Date.now() + 30 * 86400000).toISOString().slice(0, 16);
        await formSubmit(maker, { '权益版本': { label: '浏览器 Plus 权益' }, '供应商原码（每行一份，最多 100 行）': 'PLUS-BROWSER-SUPPLIER-ONLY',
            '采购凭据引用': 'BROWSER-PURCHASE', '供应商有效期（本地时间）': until });
        await maker.getByRole('button', { name: '确认导入有效行' }).click();
        await maker.getByRole('button', { name: '验证供应商库存' }).click();
        await maker.getByText('可分配', { exact: true }).waitFor();
        await selectTab(maker, '发行批次');
        await maker.getByRole('button', { name: '新建批次' }).click();
        await formSubmit(maker, { '批次名称': '浏览器首发批次', '权益版本': { label: '浏览器 Plus 权益' }, '渠道': { label: '浏览器验收渠道' },
            '生效时间（本地时间）': new Date(Date.now() - 86400000).toISOString().slice(0, 16),
            '到期时间（本地时间）': new Date(Date.now() + 7 * 86400000).toISOString().slice(0, 16) });
        await maker.getByRole('button', { name: '生成 CDK 卡密' }).click();
        await maker.getByRole('button', { name: '申请激活' }).click();
        await formSubmit(maker, { '操作原因': '独立核对测试权益与库存后激活' });
        await login(checker, 'checker');
        await selectTab(checker, '审批');
        checker.on('dialog', dialog => dialog.accept());
        await checker.getByRole('button', { name: '批准执行' }).click();
        await checker.getByText('已执行', { exact: true }).waitFor();
        await selectTab(maker, '卡密');
        await maker.getByRole('table').getByRole('button', { name: '分发', exact: true }).click();
        await formSubmit(maker, { '交接对象引用（不要填写密钥）': 'BROWSER-DELIVERY' });
        await maker.getByRole('checkbox').check();
        await maker.getByRole('button', { name: /申请导出所选/ }).click();
        await formSubmit(maker, { '操作原因': '受控导出浏览器验收测试券' });
        await checker.getByRole('button', { name: '刷新', exact: true }).click();
        await checker.getByRole('button', { name: '批准执行' }).click();
        await selectTab(maker, '导入与导出');
        const downloadEvent = maker.waitForEvent('download');
        await maker.getByRole('button', { name: '下载导出文件' }).click();
        const download = await downloadEvent;
        const content = await readFile(await download.path(), 'utf8');
        const code = content.match(/GM1[0-9A-Z]{27}/)?.[0];
        assert.ok(code, 'Export contains a local platform code');
        assert.ok(!content.includes('PLUS-BROWSER-SUPPLIER-ONLY'), 'Supplier secrets must not be exported');
        await customer.goto(baseUrl + '/');
        await customer.getByLabel('平台卡密', { exact: true }).fill(code);
        await customer.getByRole('button', { name: '验证卡密' }).click();
        await customer.getByLabel('Session JSON', { exact: true }).fill(JSON.stringify({ accessToken: 'synthetic-browser-credential', user: { email: 'browser@example.test' } }));
        await customer.getByLabel('目标账号邮箱', { exact: true }).fill('browser@example.test');
        await customer.getByRole('checkbox').check();
        await customer.getByRole('button', { name: '确认核销' }).click();
        const result = customer.getByRole('region', { name: '本次兑换结果' });
        await result.waitFor();
        await result.getByRole('button', { name: '查询最新状态' }).click();
        await result.getByRole('button', { name: '查询最新状态' }).click();
        await result.getByText('已完成', { exact: true }).waitFor();
        const dimensions = await customer.evaluate(() => [document.documentElement.scrollWidth, document.documentElement.clientWidth]);
        assert.ok(dimensions[0] <= dimensions[1] + 1, 'Mobile redemption page fits viewport');
        await selectTab(maker, '核销订单');
        await maker.getByText('成功', { exact: true }).waitFor();
        const state = await (await fetch(baseUrl + '/__test__/state')).json();
        assert.equal(state.tasks, 1);
        assert.equal(state.redemptions, 1);
        assert.deepEqual(state.cards, ['redeemed']);
        assert.deepEqual(state.stocks, ['consumed']);
        assert.equal(state.upstream.create_count, 1);
        assert.equal(state.upstream.idempotency_matches_client_task, true);
        assert.equal(state.upstream.challenge_token_forwarded, false);
        assert.deepEqual(errors, []);
    } finally {
        await browser?.close();
        if (baseUrl) await fetch(baseUrl + '/__test__/shutdown', { method: 'POST', signal: AbortSignal.timeout(3000) }).catch(() => {});
        if (fixture.exitCode === null) {
            await new Promise(resolveExit => { const timer = setTimeout(() => { fixture.kill(); resolveExit(); }, 4000); fixture.once('exit', () => { clearTimeout(timer); resolveExit(); }); });
        }
        assert.ok(directory.startsWith(join(tmpdir(), 'google-manager-cdk-flow-')));
        await rm(directory, { recursive: true, force: true });
    }
});
