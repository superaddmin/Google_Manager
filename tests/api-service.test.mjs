import assert from 'node:assert/strict';
import test from 'node:test';

import api from '../frontend/src/services/api.js';

function response(body, status = 200, contentType = 'application/json') {
  return new Response(contentType === 'application/json' ? JSON.stringify(body) : String(body), {
    status,
    headers: { 'content-type': contentType },
  });
}

test('protected API methods surface HTTP errors with status and message', async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async () => response({
    success: false,
    message: '批量导入失败，请稍后重试',
  }, 500);

  try {
    await assert.rejects(
      api.batchImport([]),
      error => error.status === 500 && error.message === '批量导入失败，请稍后重试',
    );
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test('protected API methods handle non-JSON HTTP errors', async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async () => response('upstream failure', 502, 'text/plain');

  try {
    await assert.rejects(
      api.deleteAccount(42),
      error => error.status === 502 && error.message === '删除失败',
    );
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test('login keeps structured authentication failure responses', async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async () => response({
    success: false,
    message: '密码错误',
  }, 401);

  try {
    const result = await api.login('wrong-password');
    assert.equal(result.success, false);
    assert.equal(result.message, '密码错误');
  } finally {
    globalThis.fetch = originalFetch;
  }
});
