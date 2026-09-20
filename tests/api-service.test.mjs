import assert from 'node:assert/strict';
import test from 'node:test';

import api from '../frontend/src/services/api.js';

function response(body, status = 200, contentType = 'application/json') {
  return new Response(contentType === 'application/json' ? JSON.stringify(body) : String(body), {
    status,
    headers: { 'content-type': contentType },
  });
}

async function withTestDeadline(promise, timeoutMs = 100) {
  let timer;
  try {
    return await Promise.race([
      promise,
      new Promise((_, reject) => {
        timer = setTimeout(() => reject(new Error('test deadline exceeded')), timeoutMs);
      }),
    ]);
  } finally {
    clearTimeout(timer);
  }
}

test('protected API methods surface HTTP errors with status and message', async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async () => response({
    success: false,
    message: '批量导入失败，请稍后重试',
    error_code: 'internal_error',
  }, 500);

  try {
    await assert.rejects(
      api.batchImport([]),
      error => error.status === 500 &&
        error.message === '批量导入失败，请稍后重试' &&
        error.errorCode === 'internal_error',
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

test('protected API methods reject empty and non-object HTTP error bodies without TypeError', async t => {
  const fixtures = [
    {
      name: 'empty JSON body',
      createResponse: () => new Response('', {
        status: 502,
        headers: { 'content-type': 'application/json' },
      }),
    },
    { name: 'null JSON body', createResponse: () => response(null, 502) },
    { name: 'array JSON body', createResponse: () => response([], 502) },
  ];

  for (const fixture of fixtures) {
    await t.test(fixture.name, async () => {
      const originalFetch = globalThis.fetch;
      globalThis.fetch = async () => fixture.createResponse();

      try {
        await assert.rejects(
          api.deleteAccount(42),
          error => error instanceof Error &&
            !(error instanceof TypeError) &&
            error.status === 502 &&
            error.message === '删除失败',
        );
      } finally {
        globalThis.fetch = originalFetch;
      }
    });
  }
});

test('HTTP success with a business failure rejects with the structured API error', async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async () => response({
    success: false,
    message: '卡密已被使用',
    error_code: 'redeem_code_used',
  });

  try {
    await assert.rejects(
      api.validateRedeemCode('PLUS-USED-001'),
      error => error.status === 200 &&
        error.message === '卡密已被使用' &&
        error.errorCode === 'redeem_code_used' &&
        error.response?.success === false,
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

test('authentication requests bound both network and response-body waits', async t => {
  const calls = [
    ['login', options => api.login('synthetic-password', options)],
    ['check', options => api.checkAuth(options)],
    ['logout', options => api.logout(options)],
  ];
  for (const [name, call] of calls) {
    for (const stage of ['network', 'body']) {
      await t.test(`${name}: ${stage}`, async () => {
        const originalFetch = globalThis.fetch;
        globalThis.fetch = stage === 'network'
          ? () => new Promise(() => {})
          : async () => ({ status: 200, json: () => new Promise(() => {}) });
        try {
          await assert.rejects(
            withTestDeadline(call({ timeoutMs: 10 })),
            error => error.status === 408,
          );
        } finally {
          globalThis.fetch = originalFetch;
        }
      });
    }
  }
});

test('authentication responses reject null and honor caller cancellation', async () => {
  const originalFetch = globalThis.fetch;
  try {
    globalThis.fetch = async () => response(null);
    await assert.rejects(api.checkAuth(), error => error.message === '检查登录状态失败');
    globalThis.fetch = () => new Promise(() => {});
    const controller = new AbortController();
    const pending = api.logout({ signal: controller.signal });
    controller.abort();
    await assert.rejects(withTestDeadline(pending), error => error.name === 'AbortError');
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test('all API write request paths add the XMLHttpRequest CSRF header', async () => {
  const originalFetch = globalThis.fetch;
  const requests = [];
  globalThis.fetch = async (url, options = {}) => {
    requests.push({ url, options });
    return response({ success: true, data: {} });
  };

  try {
    await api.login('fixture-password');
    await api.batchImport([]);
    await api.updateAccount(1, { remark: 'fixture' });
    await api.deleteAccount(1);
    await api.toggleStatus(1);
    await api.downloadRechargeInvoice('PLUS-SYNTHETIC-INVOICE');
    await api.logout();

    assert.equal(requests.length, 7);
    for (const request of requests) {
      const headers = new Headers(request.options.headers);
      assert.equal(
        headers.get('X-Requested-With'),
        'XMLHttpRequest',
        `${request.options.method} ${request.url} must carry the CSRF request header`,
      );
    }
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test('TASK- is not treated as a task number when the backend only accepts TK-', async () => {
  const originalFetch = globalThis.fetch;
  const requests = [];
  globalThis.fetch = async (url, options = {}) => {
    requests.push({ url, options });
    return response({ success: true, data: { status: 'idle' } });
  };

  try {
    await api.lookupRechargeTask('TASK-CLIENT-001');
    assert.equal(requests.length, 1);
    assert.equal(new URL(requests[0].url, 'http://localhost').pathname, '/api/recharge/tasks/lookup');
    assert.deepEqual(JSON.parse(requests[0].options.body), { redeem_code: 'TASK-CLIENT-001' });
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test('recall and close requests include the task number required by the backend', async () => {
  const originalFetch = globalThis.fetch;
  const requests = [];
  globalThis.fetch = async (url, options = {}) => {
    requests.push({ url, options });
    return response({ success: true, data: {} });
  };

  try {
    await api.recallRechargeTask('PLUS-RECALL-001', 'owner@example.com', true, 'TK-RECALL-001');
    await api.closeRechargeTask('PLUS-CLOSE-001', 'owner@example.com', true, 'TK-CLOSE-001');

    assert.equal(requests.length, 2);
    assert.deepEqual(JSON.parse(requests[0].options.body), {
      redeem_code: 'PLUS-RECALL-001',
      email: 'owner@example.com',
      confirmed: true,
      task_no: 'TK-RECALL-001',
    });
    assert.deepEqual(JSON.parse(requests[1].options.body), {
      redeem_code: 'PLUS-CLOSE-001',
      email: 'owner@example.com',
      confirmed: true,
      task_no: 'TK-CLOSE-001',
    });
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test('requestJson aborts a hanging request with a stable timeout error', async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = (_url, options = {}) => new Promise((resolve, reject) => {
    const abort = () => {
      const error = new DOMException('The operation was aborted.', 'AbortError');
      reject(error);
    };
    if (options.signal?.aborted) abort();
    else options.signal?.addEventListener('abort', abort, { once: true });
  });

  try {
    await assert.rejects(
      api.validateRedeemCode('PLUS-TIMEOUT-001', { timeoutMs: 10 }),
      error => error.status === 408 && error.message === 'CDK 卡密验证失败',
    );
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test('requestJson timeout remains active while the JSON response body is pending', async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async () => ({
    ok: true,
    status: 200,
    json: () => new Promise(() => {}),
  });

  try {
    await assert.rejects(
      withTestDeadline(api.validateRedeemCode('PLUS-BODY-TIMEOUT-001', { timeoutMs: 10 })),
      error => error.status === 408 && error.message === 'CDK 卡密验证失败',
    );
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test('requestJson honors external cancellation while the JSON response body is pending', async () => {
  const originalFetch = globalThis.fetch;
  let resolveBodyStarted;
  const bodyStarted = new Promise(resolve => {
    resolveBodyStarted = resolve;
  });
  globalThis.fetch = async () => ({
    ok: true,
    status: 200,
    json: () => {
      resolveBodyStarted();
      return new Promise(() => {});
    },
  });

  try {
    const controller = new AbortController();
    const request = api.getRechargeConfig({ signal: controller.signal });
    await bodyStarted;
    controller.abort();
    await assert.rejects(
      withTestDeadline(request),
      error => error.name === 'AbortError',
    );
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test('invoice download timeout remains active while the blob body is pending', async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async () => ({
    ok: true,
    status: 200,
    blob: () => new Promise(() => {}),
  });

  try {
    await assert.rejects(
      withTestDeadline(api.downloadRechargeInvoice(
        'PLUS-BODY-TIMEOUT-INVOICE',
        'redeem_code',
        { timeoutMs: 10 },
      )),
      error => error.status === 408 && error.message === '下载对账凭证超时，请稍后重试',
    );
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test('invoice download preserves structured HTTP error details', async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async () => response({
    success: false,
    message: '对账凭证尚未生成',
    error_code: 'invoice_not_ready',
  }, 409);

  try {
    await assert.rejects(
      api.downloadRechargeInvoice('PLUS-INVOICE-NOT-READY'),
      error => error.status === 409 &&
        error.message === '对账凭证尚未生成' &&
        error.errorCode === 'invoice_not_ready' &&
        error.response?.success === false,
    );
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test('recharge metadata requests honor a shared cancellation signal', async () => {
  const originalFetch = globalThis.fetch;
  let requestCount = 0;
  let resolveStarted;
  const started = new Promise(resolve => {
    resolveStarted = resolve;
  });
  globalThis.fetch = (_url, options = {}) => new Promise((resolve, reject) => {
    requestCount += 1;
    if (requestCount === 3) resolveStarted();
    const abort = () => reject(new DOMException('The operation was aborted.', 'AbortError'));
    if (options.signal?.aborted) abort();
    else options.signal?.addEventListener('abort', abort, { once: true });
  });

  try {
    const controller = new AbortController();
    const requests = [
      api.getRechargeConfig({ signal: controller.signal }),
      api.getRechargeAgreement({ signal: controller.signal }),
      api.getRechargeAvgTime('gpt', 'card', { signal: controller.signal }),
    ];
    await started;
    controller.abort();
    const results = await Promise.allSettled(requests);
    assert.equal(requestCount, 3);
    assert.equal(results.every(result => result.status === 'rejected' && result.reason.name === 'AbortError'), true);
  } finally {
    globalThis.fetch = originalFetch;
  }
});
