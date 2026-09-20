import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { chromium } from 'playwright';
import {
  assertExpectedChromeVersion,
  cleanupBrowserStartup,
  getBrowserLaunchOptions,
} from './browser-runtime.mjs';

const PAGE_URL = 'data:text/html,<title>startup-check</title><main>ok</main>';

async function verifyLocalPage(page, launchMode) {
  await page.goto(PAGE_URL);
  const result = await page.evaluate(() => ({
    answer: 21 * 2,
    locale: navigator.language,
    title: document.title,
  }));
  const screenshot = await page.screenshot({ type: 'png' });

  if (result.title !== 'startup-check' || result.answer !== 42) {
    throw new Error(`${launchMode} 本地 JavaScript 页面未按预期加载`);
  }
  if (result.locale.toLowerCase() !== 'zh-cn') {
    throw new Error(`${launchMode} 浏览器 locale 不是 zh-CN`);
  }
  if (!Buffer.isBuffer(screenshot) || screenshot.length === 0) {
    throw new Error(`${launchMode} 本地截图为空`);
  }
}

let browser;
let browserContext;
let persistentContext;
let temporaryProfile;
let startupPassed = false;

try {
  browser = await chromium.launch(getBrowserLaunchOptions({ headless: true }));
  assertExpectedChromeVersion(browser.version());
  browserContext = await browser.newContext({ locale: 'zh-CN' });
  await verifyLocalPage(await browserContext.newPage(), 'launch');

  temporaryProfile = await fs.promises.mkdtemp(
    path.join(os.tmpdir(), 'google-manager-startup-'),
  );
  persistentContext = await chromium.launchPersistentContext(
    temporaryProfile,
    getBrowserLaunchOptions({ headless: true, locale: 'zh-CN' }),
  );
  const persistentBrowser = persistentContext.browser();
  if (!persistentBrowser) {
    throw new Error('launchPersistentContext 未返回浏览器实例');
  }
  assertExpectedChromeVersion(persistentBrowser.version());
  await verifyLocalPage(await persistentContext.newPage(), 'launchPersistentContext');

  startupPassed = true;
} catch (error) {
  console.error(`[启动检查] 失败: ${error.message}`);
  process.exitCode = 1;
} finally {
  try {
    await cleanupBrowserStartup(
      { persistentContext, browserContext, browser, temporaryProfile },
      fs.promises.rm,
    );
  } catch {
    console.error('[启动检查] 失败: 浏览器资源或临时 profile 清理未完成');
    process.exitCode = 1;
  }
  if (startupPassed && !process.exitCode) {
    console.log('[启动检查] Chromium 两类启动、本地脚本、locale、截图与清理通过');
  }
}
