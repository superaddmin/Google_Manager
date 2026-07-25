import { chromium } from 'playwright';

let browser;

try {
  browser = await chromium.launch({ headless: true });
  const page = await browser.newPage();
  await page.goto('data:text/html,<title>startup-check</title><main>ok</main>');

  if (await page.title() !== 'startup-check') {
    throw new Error('浏览器页面未按预期加载');
  }

  console.log('[启动检查] Chromium 启动与本地页面加载通过');
} catch (error) {
  console.error(`[启动检查] 失败: ${error.message}`);
  process.exitCode = 1;
} finally {
  await browser?.close();
}
