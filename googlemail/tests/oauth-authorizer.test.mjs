import { afterEach, describe, expect, it, vi } from 'vitest';
import { authorizeGoogleOAuth } from '../src/oauth-authorizer.mjs';

const account = { email: 'fixture@example.test', password: 'fixture-password', recovery: '' };
const callback = 'https://manager.example.test/api/gmail/oauth/callback';
const authorization = `https://accounts.google.com/o/oauth2/auth?redirect_uri=${encodeURIComponent(callback)}&state=fixture-state`;

function createPage({ url = `${callback}?state=fixture-state&code=fixture-code`, body, visible } = {}) {
  let now = 0;
  vi.spyOn(Date, 'now').mockImplementation(() => now);
  return {
    addInitScript: vi.fn(async () => {}),
    goto: vi.fn(async () => {}),
    waitForTimeout: vi.fn(async ms => { now += ms; }),
    url: vi.fn(() => url),
    waitForLoadState: vi.fn(async () => {}),
    innerText: vi.fn(async selector => selector === 'body' ? body ?? '' : ''),
    $: vi.fn(async () => null),
    $$: vi.fn(async () => []),
    isVisible: vi.fn(async selector => visible?.(selector) ?? false),
    screenshot: vi.fn(async () => {}),
    keyboard: { press: vi.fn(async () => {}) },
  };
}

afterEach(() => vi.restoreAllMocks());

describe('OAuth completion and failure boundaries', () => {
  it('accepts a successful callback for the expected state and account', async () => {
    const page = createPage({ body: JSON.stringify({ success: true, data: { email: account.email } }) });
    const logs = [];
    expect(await authorizeGoogleOAuth(page, account, authorization, line => logs.push(line)))
      .toEqual({ success: true, email: account.email });
    expect(logs.join('\n')).not.toContain('fixture-state');
    expect(page.screenshot).not.toHaveBeenCalled();
  });

  it.each([
    ['explicit failure containing success text', JSON.stringify({ success: false, message: '授权成功并未完成' })],
    ['malformed response', 'Gmail 授权成功'],
    ['different account', JSON.stringify({ success: true, data: { email: 'other@example.test' } })],
    ['missing account', JSON.stringify({ success: true })],
  ])('rejects %s', async (_label, body) => {
    const result = await authorizeGoogleOAuth(createPage({ body }), account, authorization, vi.fn(), { timeoutMs: 5000 });
    expect(result.success).toBe(false);
    expect(result.error).toBeTruthy();
  });

  it.each([
    `${callback}?state=other-state`,
    `${callback}?code=fixture-code`,
    `https://other.example.test/api/gmail/oauth/callback?state=fixture-state`,
  ])('rejects an unrelated callback at %s', async url => {
    const page = createPage({ url, body: JSON.stringify({ success: true, data: { email: account.email } }) });
    const result = await authorizeGoogleOAuth(page, account, authorization, vi.fn(), { timeoutMs: 5000 });
    expect(result.success).toBe(false);
  });

  it('times out an unrecognized page without reporting success', async () => {
    const page = createPage({ url: 'https://accounts.google.com/unrecognized' });
    const result = await authorizeGoogleOAuth(page, account, authorization, vi.fn(), { timeoutMs: 5000 });
    expect(result).toMatchObject({ success: false, error: expect.stringContaining('超时') });
    expect(page.screenshot).toHaveBeenCalledOnce();
  });

  it.each(['waitForLoadState', 'innerText'])('rejects navigation during %s', async method => {
    const body = JSON.stringify({ success: true, data: { email: account.email } });
    const page = createPage({ body });
    page[method].mockImplementation(async () => {
      page.url.mockReturnValue('https://other.example.test/replaced');
      return body;
    });
    const result = await authorizeGoogleOAuth(page, account, authorization, vi.fn());
    expect(result).toMatchObject({ success: false, error: expect.stringContaining('跳转') });
  });

  it('does not echo credentials or authorization URLs after a browser failure', async () => {
    const page = createPage();
    page.goto.mockRejectedValue(new Error(`network ${account.password} ${authorization}`));
    const logs = [];
    const result = await authorizeGoogleOAuth(page, account, authorization, line => logs.push(line));
    expect(result.success).toBe(false);
    for (const text of [JSON.stringify(result), logs.join('\n')]) {
      expect(text).not.toContain(account.password);
      expect(text).not.toContain('fixture-state');
      expect(text).not.toContain(authorization);
    }
  });

  it('fails clearly when a required TOTP secret is missing', async () => {
    const page = createPage({ url: 'https://accounts.google.com/challenge', visible: selector => selector.includes('one-time-code') });
    const result = await authorizeGoogleOAuth(page, account, authorization, vi.fn());
    expect(result).toMatchObject({ success: false, error: expect.stringContaining('未提供') });
  });

  it('bounds a recovery challenge whose selection never advances', async () => {
    const page = createPage({ url: 'https://accounts.google.com/challenge' });
    const choice = { isVisible: vi.fn(async () => true), click: vi.fn(async () => {}) };
    page.$.mockImplementation(async selector => {
      if (!selector.includes('data-challengetype')) return null;
      if (choice.click.mock.calls.length >= 4) throw new Error('unbounded recovery selection');
      return choice;
    });
    const result = await authorizeGoogleOAuth(page, { ...account, recovery: 'recovery@example.test' }, authorization, vi.fn());
    expect(result).toMatchObject({ success: false, error: expect.stringContaining('辅助邮箱') });
    expect(choice.click.mock.calls.length).toBeLessThanOrEqual(3);
  });
});
