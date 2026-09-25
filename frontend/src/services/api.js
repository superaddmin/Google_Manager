// API 调用函数
const API_BASE = '/api';
const DEFAULT_REQUEST_TIMEOUT_MS = 20000;

// 生成盐值：时间戳减去2003，再 MD5 哈希
const generateSalt = () => {
    const timestamp = Math.floor(Date.now() / 1000); // 当前时间戳（秒）
    const saltBase = String(timestamp - 2003);
    // 使用 Web Crypto API 的替代方案：简单的 MD5 实现
    return md5(saltBase);
};

// 简单的 MD5 实现（用于盐值生成）
const md5 = (string) => {
    function md5cycle(x, k) {
        var a = x[0], b = x[1], c = x[2], d = x[3];
        a = ff(a, b, c, d, k[0], 7, -680876936);
        d = ff(d, a, b, c, k[1], 12, -389564586);
        c = ff(c, d, a, b, k[2], 17, 606105819);
        b = ff(b, c, d, a, k[3], 22, -1044525330);
        a = ff(a, b, c, d, k[4], 7, -176418897);
        d = ff(d, a, b, c, k[5], 12, 1200080426);
        c = ff(c, d, a, b, k[6], 17, -1473231341);
        b = ff(b, c, d, a, k[7], 22, -45705983);
        a = ff(a, b, c, d, k[8], 7, 1770035416);
        d = ff(d, a, b, c, k[9], 12, -1958414417);
        c = ff(c, d, a, b, k[10], 17, -42063);
        b = ff(b, c, d, a, k[11], 22, -1990404162);
        a = ff(a, b, c, d, k[12], 7, 1804603682);
        d = ff(d, a, b, c, k[13], 12, -40341101);
        c = ff(c, d, a, b, k[14], 17, -1502002290);
        b = ff(b, c, d, a, k[15], 22, 1236535329);
        a = gg(a, b, c, d, k[1], 5, -165796510);
        d = gg(d, a, b, c, k[6], 9, -1069501632);
        c = gg(c, d, a, b, k[11], 14, 643717713);
        b = gg(b, c, d, a, k[0], 20, -373897302);
        a = gg(a, b, c, d, k[5], 5, -701558691);
        d = gg(d, a, b, c, k[10], 9, 38016083);
        c = gg(c, d, a, b, k[15], 14, -660478335);
        b = gg(b, c, d, a, k[4], 20, -405537848);
        a = gg(a, b, c, d, k[9], 5, 568446438);
        d = gg(d, a, b, c, k[14], 9, -1019803690);
        c = gg(c, d, a, b, k[3], 14, -187363961);
        b = gg(b, c, d, a, k[8], 20, 1163531501);
        a = gg(a, b, c, d, k[13], 5, -1444681467);
        d = gg(d, a, b, c, k[2], 9, -51403784);
        c = gg(c, d, a, b, k[7], 14, 1735328473);
        b = gg(b, c, d, a, k[12], 20, -1926607734);
        a = hh(a, b, c, d, k[5], 4, -378558);
        d = hh(d, a, b, c, k[8], 11, -2022574463);
        c = hh(c, d, a, b, k[11], 16, 1839030562);
        b = hh(b, c, d, a, k[14], 23, -35309556);
        a = hh(a, b, c, d, k[1], 4, -1530992060);
        d = hh(d, a, b, c, k[4], 11, 1272893353);
        c = hh(c, d, a, b, k[7], 16, -155497632);
        b = hh(b, c, d, a, k[10], 23, -1094730640);
        a = hh(a, b, c, d, k[13], 4, 681279174);
        d = hh(d, a, b, c, k[0], 11, -358537222);
        c = hh(c, d, a, b, k[3], 16, -722521979);
        b = hh(b, c, d, a, k[6], 23, 76029189);
        a = hh(a, b, c, d, k[9], 4, -640364487);
        d = hh(d, a, b, c, k[12], 11, -421815835);
        c = hh(c, d, a, b, k[15], 16, 530742520);
        b = hh(b, c, d, a, k[2], 23, -995338651);
        a = ii(a, b, c, d, k[0], 6, -198630844);
        d = ii(d, a, b, c, k[7], 10, 1126891415);
        c = ii(c, d, a, b, k[14], 15, -1416354905);
        b = ii(b, c, d, a, k[5], 21, -57434055);
        a = ii(a, b, c, d, k[12], 6, 1700485571);
        d = ii(d, a, b, c, k[3], 10, -1894986606);
        c = ii(c, d, a, b, k[10], 15, -1051523);
        b = ii(b, c, d, a, k[1], 21, -2054922799);
        a = ii(a, b, c, d, k[8], 6, 1873313359);
        d = ii(d, a, b, c, k[15], 10, -30611744);
        c = ii(c, d, a, b, k[6], 15, -1560198380);
        b = ii(b, c, d, a, k[13], 21, 1309151649);
        a = ii(a, b, c, d, k[4], 6, -145523070);
        d = ii(d, a, b, c, k[11], 10, -1120210379);
        c = ii(c, d, a, b, k[2], 15, 718787259);
        b = ii(b, c, d, a, k[9], 21, -343485551);
        x[0] = add32(a, x[0]);
        x[1] = add32(b, x[1]);
        x[2] = add32(c, x[2]);
        x[3] = add32(d, x[3]);
    }
    function cmn(q, a, b, x, s, t) {
        a = add32(add32(a, q), add32(x, t));
        return add32((a << s) | (a >>> (32 - s)), b);
    }
    function ff(a, b, c, d, x, s, t) { return cmn((b & c) | ((~b) & d), a, b, x, s, t); }
    function gg(a, b, c, d, x, s, t) { return cmn((b & d) | (c & (~d)), a, b, x, s, t); }
    function hh(a, b, c, d, x, s, t) { return cmn(b ^ c ^ d, a, b, x, s, t); }
    function ii(a, b, c, d, x, s, t) { return cmn(c ^ (b | (~d)), a, b, x, s, t); }
    function md51(s) {
        var n = s.length, state = [1732584193, -271733879, -1732584194, 271733878], i;
        for (i = 64; i <= s.length; i += 64) { md5cycle(state, md5blk(s.substring(i - 64, i))); }
        s = s.substring(i - 64);
        var tail = [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0];
        for (i = 0; i < s.length; i++) tail[i >> 2] |= s.charCodeAt(i) << ((i % 4) << 3);
        tail[i >> 2] |= 0x80 << ((i % 4) << 3);
        if (i > 55) { md5cycle(state, tail); for (i = 0; i < 16; i++) tail[i] = 0; }
        tail[14] = n * 8;
        md5cycle(state, tail);
        return state;
    }
    function md5blk(s) {
        var md5blks = [], i;
        for (i = 0; i < 64; i += 4) {
            md5blks[i >> 2] = s.charCodeAt(i) + (s.charCodeAt(i + 1) << 8) + (s.charCodeAt(i + 2) << 16) + (s.charCodeAt(i + 3) << 24);
        }
        return md5blks;
    }
    var hex_chr = '0123456789abcdef'.split('');
    function rhex(n) {
        var s = '', j = 0;
        for (; j < 4; j++) s += hex_chr[(n >> (j * 8 + 4)) & 0x0F] + hex_chr[(n >> (j * 8)) & 0x0F];
        return s;
    }
    function hex(x) { for (var i = 0; i < x.length; i++) x[i] = rhex(x[i]); return x.join(''); }
    function add32(a, b) { return (a + b) & 0xFFFFFFFF; }
    return hex(md51(string));
};

const createApiError = (message, status) => {
    const error = new Error(message);
    if (status !== undefined) error.status = status;
    return error;
};

const isJsonObject = (value) => value !== null && typeof value === 'object' && !Array.isArray(value);

const createResponseError = (data, fallbackMessage, status) => {
    const payload = isJsonObject(data) ? data : {};
    const message = typeof payload.message === 'string' && payload.message
        ? payload.message
        : fallbackMessage;
    const error = createApiError(message, status);
    const errorCode = payload.error_code ?? payload.errorCode;
    if (errorCode !== undefined) error.errorCode = errorCode;
    error.response = data;
    return error;
};

const parseJsonResponse = async (res, fallbackMessage) => {
    try {
        return await res.json();
    } catch {
        throw createApiError(fallbackMessage, res.status);
    }
};

export const fetchWithTimeout = async (
    url,
    options = {},
    fallbackMessage,
    timeoutMs = DEFAULT_REQUEST_TIMEOUT_MS,
    readResponse = response => response
) => {
    const controller = new AbortController();
    const upstreamSignal = options.signal;
    let removeAbortListener;
    let rejectInterruption;
    const interruption = new Promise((_, reject) => {
        rejectInterruption = reject;
    });

    const abortFromUpstream = () => {
        const reason = upstreamSignal.reason || new DOMException('The operation was aborted.', 'AbortError');
        rejectInterruption(reason);
        controller.abort(reason);
    };

    if (upstreamSignal) {
        if (upstreamSignal.aborted) {
            abortFromUpstream();
        } else {
            upstreamSignal.addEventListener('abort', abortFromUpstream, { once: true });
            removeAbortListener = () => upstreamSignal.removeEventListener('abort', abortFromUpstream);
        }
    }

    const timer = setTimeout(() => {
        const error = createApiError(fallbackMessage || '请求超时，请稍后重试', 408);
        rejectInterruption(error);
        controller.abort(error);
    }, timeoutMs);

    try {
        const request = (async () => {
            const response = await fetch(url, { ...options, signal: controller.signal });
            return await readResponse(response);
        })();
        return await Promise.race([request, interruption]);
    } finally {
        clearTimeout(timer);
        removeAbortListener?.();
    }
};

const WRITE_METHODS = new Set(['POST', 'PUT', 'PATCH', 'DELETE']);

const withWriteRequestHeaders = (options) => {
    const method = String(options?.method || 'GET').toUpperCase();
    if (!WRITE_METHODS.has(method)) return options;
    return {
        ...options,
        headers: {
            ...(options?.headers || {}),
            'X-Requested-With': 'XMLHttpRequest'
        }
    };
};

export const requestJson = async (url, options, fallbackMessage) => {
    const { timeoutMs, ...fetchOptions } = options || {};
    return await fetchWithTimeout(
        url,
        withWriteRequestHeaders(fetchOptions),
        fallbackMessage,
        timeoutMs,
        async res => {
            const data = await parseJsonResponse(res, fallbackMessage);
            if (!isJsonObject(data) || !res.ok || data.success === false) {
                throw createResponseError(data, fallbackMessage, res.status);
            }
            return data;
        }
    );
};

// 登录页需要保留密码错误和封禁响应，但网络与响应体读取同样必须有期限。
const requestAuthJson = async (url, options, fallbackMessage) => {
    const { timeoutMs, ...fetchOptions } = options || {};
    return await fetchWithTimeout(
        url,
        withWriteRequestHeaders(fetchOptions),
        fallbackMessage,
        timeoutMs,
        async res => {
            const data = await parseJsonResponse(res, fallbackMessage);
            if (!isJsonObject(data)) throw createApiError(fallbackMessage, res.status);
            return data;
        }
    );
};

const api = {
    // 登录验证（带盐值）
    async login(password, options = {}) {
        const salt = generateSalt();
        return await requestAuthJson(`${API_BASE}/auth/login`, {
            signal: options.signal,
            timeoutMs: options.timeoutMs,
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ password, salt })
        }, '登录失败');
    },

    // 检查封禁状态
    async checkAuth(options = {}) {
        return await requestAuthJson(`${API_BASE}/auth/check`, {
            signal: options.signal,
            timeoutMs: options.timeoutMs,
        }, '检查登录状态失败');
    },

    // 获取所有账号
    async getAccounts(search = '') {
        const url = search
            ? `${API_BASE}/accounts?search=${encodeURIComponent(search)}`
            : `${API_BASE}/accounts`;
        const data = await requestJson(url, undefined, '加载账号失败');
        if (!data.success) {
            throw createApiError(data.message || '加载账号失败', 200);
        }
        return data.data;
    },

    // 批量导入账号
    async batchImport(accounts) {
        return await requestJson(`${API_BASE}/accounts/batch`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ accounts })
        }, '批量导入失败');
    },

    // 更新账号
    async updateAccount(id, data) {
        return await requestJson(`${API_BASE}/accounts/${id}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(data)
        }, '更新失败');
    },

    // 删除账号
    async deleteAccount(id) {
        return await requestJson(`${API_BASE}/accounts/${id}`, {
            method: 'DELETE'
        }, '删除失败');
    },

    // 切换状态
    async toggleStatus(id) {
        return await requestJson(`${API_BASE}/accounts/${id}/status`, {
            method: 'PATCH'
        }, '切换状态失败');
    },

    // 切换出售状态
    async toggleSoldStatus(id) {
        return await requestJson(`${API_BASE}/accounts/${id}/sold`, {
            method: 'PATCH'
        }, '切换出售状态失败');
    },

    // 获取 2FA 验证码
    async get2FACode(id) {
        return await requestJson(`${API_BASE}/accounts/${id}/2fa`, undefined, '获取验证码失败');
    },

    // 获取账号修改历史记录
    async getAccountHistory(id) {
        return await requestJson(`${API_BASE}/accounts/${id}/history`, undefined, '加载历史记录失败');
    },

    // 构建导出链接（带当前筛选条件）
    buildExportUrl(search = '', sold = 'all', format = 'csv') {
        const params = new URLSearchParams({ format, sold });
        if (search) params.set('search', search);
        return `${API_BASE}/accounts/export?${params.toString()}`;
    },

    // 获取账号资产统计
    async getStats() {
        return await requestJson(`${API_BASE}/stats`, undefined, '获取统计信息失败');
    },

    // 批量删除账号
    async batchDeleteAccounts(accountIds) {
        return await requestJson(`${API_BASE}/accounts/batch-delete`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ accountIds })
        }, '批量删除失败');
    },

    // 批量设置出售状态
    async batchSetSoldStatus(accountIds, status) {
        return await requestJson(`${API_BASE}/accounts/batch-sold`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ accountIds, status })
        }, '批量更新出售状态失败');
    },

    // 批量设置备注
    async batchSetRemark(accountIds, remark) {
        return await requestJson(`${API_BASE}/accounts/batch-remark`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ accountIds, remark })
        }, '批量更新备注失败');
    },

    async getGooglemailStatus() {
        return await requestJson(`${API_BASE}/googlemail/status`, undefined, '获取 Googlemail 状态失败');
    },

    async startGooglemailTask(accountIds, options) {
        return await requestJson(`${API_BASE}/googlemail/tasks`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ accountIds, options })
        }, '启动 Googlemail 任务失败');
    },

    async getGooglemailTask(taskId) {
        return await requestJson(`${API_BASE}/googlemail/tasks/${taskId}`, undefined, '获取 Googlemail 任务失败');
    },

    // 获取 Googlemail 任务历史
    async getGooglemailTasks(limit = 20) {
        return await requestJson(`${API_BASE}/googlemail/tasks?limit=${limit}`, undefined, '获取 Googlemail 任务历史失败');
    },

    async cancelGooglemailTask(taskId) {
        return await requestJson(`${API_BASE}/googlemail/tasks/${taskId}/cancel`, {
            method: 'POST'
        }, '取消 Googlemail 任务失败');
    },

    async startGmailOAuth() {
        return await requestJson(`${API_BASE}/gmail/oauth/start`, undefined, '启动 Gmail 授权失败');
    },

    async getGmailConnections() {
        return await requestJson(`${API_BASE}/gmail/connections`, undefined, '获取 Gmail 账号失败');
    },

    async getGmailMessages(connectionId, query = '', pageToken = '') {
        const params = new URLSearchParams({ maxResults: '20' });
        if (query) params.set('q', query);
        if (pageToken) params.set('pageToken', pageToken);
        return await requestJson(`${API_BASE}/gmail/${connectionId}/messages?${params}`, undefined, '加载 Gmail 收件箱失败');
    },

    async getGmailMessage(connectionId, messageId) {
        return await requestJson(`${API_BASE}/gmail/${connectionId}/messages/${messageId}`, undefined, '加载 Gmail 邮件失败');
    },

    async markGmailMessageRead(connectionId, messageId) {
        return await requestJson(`${API_BASE}/gmail/${connectionId}/messages/${messageId}/read`, { method: 'PATCH' }, '标记邮件失败');
    },

    async archiveGmailMessage(connectionId, messageId) {
        return await requestJson(`${API_BASE}/gmail/${connectionId}/messages/${messageId}/archive`, { method: 'PATCH' }, '归档邮件失败');
    },

    // 集中安全与防盗 API
    async getSecurityOverview() {
        return await requestJson(`${API_BASE}/security/overview`, undefined, '加载安全总览失败');
    },

    async getSecurityAccounts(filter = 'all') {
        const params = new URLSearchParams({ filter });
        return await requestJson(`${API_BASE}/security/accounts?${params}`, undefined, '加载安全账号列表失败');
    },

    async auditForwardingRules() {
        return await requestJson(`${API_BASE}/security/forwarding-audit`, undefined, '扫描外部转发规则失败');
    },

    async getCentralOTPs(limit = 10) {
        const params = new URLSearchParams({ limit: String(limit) });
        return await requestJson(`${API_BASE}/security/central-otps?${params}`, undefined, '加载集中验证码失败');
    },

    async emergencyLockAccount(accountId, reason = '异常防盗锁定') {
        return await requestJson(`${API_BASE}/security/accounts/${accountId}/lock`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ reason })
        }, '应急锁定失败');
    },

    async unlockAccount(accountId) {
        return await requestJson(`${API_BASE}/security/accounts/${accountId}/unlock`, {
            method: 'POST'
        }, '解除锁定失败');
    },

    // 批量 OAuth 2.0 自动授权与挂机收信 API
    async startBatchOAuth(accountIds, options = {}) {
        return await requestJson(`${API_BASE}/gmail/batch-authorize`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ accountIds, options })
        }, '启动批量自动授权失败');
    },

    async getBatchOAuthStatus() {
        return await requestJson(`${API_BASE}/gmail/batch-authorize/status`, undefined, '获取批量授权状态失败');
    },

    async cancelBatchOAuth(taskId) {
        return await requestJson(`${API_BASE}/gmail/batch-authorize/cancel`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ taskId })
        }, '取消批量授权失败');
    },

    async getGmailDaemonStatus() {
        return await requestJson(`${API_BASE}/gmail/daemon/status`, undefined, '获取挂机收信状态失败');
    },

    async startGmailDaemon(intervalSeconds = 180) {
        return await requestJson(`${API_BASE}/gmail/daemon/start`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ intervalSeconds })
        }, '启动挂机收信失败');
    },

    async stopGmailDaemon() {
        return await requestJson(`${API_BASE}/gmail/daemon/stop`, {
            method: 'POST'
        }, '停止挂机收信失败');
    },

    async syncGmailDaemonNow() {
        return await requestJson(`${API_BASE}/gmail/daemon/sync-now`, {
            method: 'POST'
        }, '立即触发全量同步失败');
    },

    // 充值与交付管理 API
    async getRechargeConfig(options = {}) {
        return await requestJson(`${API_BASE}/recharge/config`, {
            signal: options.signal
        }, '获取充值配置失败');
    },

    async getRechargeAgreement(options = {}) {
        return await requestJson(`${API_BASE}/recharge/agreement`, {
            signal: options.signal
        }, '获取充值协议失败');
    },

    async getRechargeAvgTime(product = 'gpt', category = 'card', options = {}) {
        return await requestJson(`${API_BASE}/recharge/stats/avg-processing-time?product=${product}&category=${category}`, {
            signal: options.signal
        }, '获取平均耗时失败');
    },

    async validateRedeemCode(redeemCode, options = {}) {
        return await requestJson(`${API_BASE}/recharge/redeem-codes/validate`, {
            method: 'POST',
            signal: options.signal,
            timeoutMs: options.timeoutMs,
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ redeem_code: redeemCode })
        }, 'CDK 卡密验证失败');
    },

    async getSubmissionChallenge(redeemCode, payload = {}) {
        return await requestJson(`${API_BASE}/recharge/submission-challenges`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ redeem_code: redeemCode, ...payload })
        }, '获取校验令牌失败');
    },

    async createRechargeTask(taskPayload) {
        return await requestJson(`${API_BASE}/recharge/tasks`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(taskPayload)
        }, '提交充值任务失败');
    },

    async getRechargeTask(taskNo, options = {}) {
        return await requestJson(`${API_BASE}/recharge/tasks/${encodeURIComponent(taskNo)}`, {
            method: 'GET',
            signal: options.signal
        }, '获取任务详情失败');
    },

    async lookupRechargeTask(query, options = {}) {
        const trimmed = String(query || '').trim();
        if (trimmed.toUpperCase().startsWith('TK-')) {
            return await this.getRechargeTask(trimmed, options);
        }
        return await requestJson(`${API_BASE}/recharge/tasks/lookup`, {
            method: 'POST',
            signal: options.signal,
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ redeem_code: trimmed })
        }, '查询任务进度失败');
    },

    async lookupBatchRechargeTasks(redeemCodes, options = {}) {
        return await requestJson(`${API_BASE}/recharge/tasks/lookup-batch`, {
            method: 'POST',
            signal: options.signal,
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ redeem_codes: redeemCodes })
        }, '批量查询任务失败');
    },

    async downloadRechargeInvoice(identifier, identifierType = 'redeem_code', options = {}) {
        const payload = identifierType === 'slug'
            ? { slug: identifier }
            : { redeem_code: identifier };
        return await fetchWithTimeout(
            `${API_BASE}/recharge/tasks/invoice/download`,
            withWriteRequestHeaders({
                method: 'POST',
                signal: options.signal,
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            }),
            '下载对账凭证超时，请稍后重试',
            options.timeoutMs,
            async res => {
                if (!res.ok) {
                    let data;
                    try {
                        data = await res.json();
                    } catch (error) {
                        if (error?.name === 'AbortError') throw error;
                    }
                    throw createResponseError(data, '下载对账凭证失败', res.status);
                }
                return await res.blob();
            }
        );
    },

    async recallRechargeTask(redeemCode, email, confirmed = true, taskNo) {
        return await requestJson(`${API_BASE}/recharge/tasks/recall`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ redeem_code: redeemCode, email, confirmed, task_no: taskNo })
        }, '撤回任务失败');
    },

    async closeRechargeTask(redeemCode, email, confirmed = true, taskNo) {
        return await requestJson(`${API_BASE}/recharge/tasks/close`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ redeem_code: redeemCode, email, confirmed, task_no: taskNo })
        }, '关闭任务失败');
    },

    async queryBilling(tokenInput, options = {}) {
        return await requestJson(`${API_BASE}/recharge/billing/query`, {
            method: 'POST',
            signal: options.signal,
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ token_input: tokenInput })
        }, '查询账单信息失败');
    },

    async cancelSubscription(tokenInput, confirmed = true) {
        return await requestJson(`${API_BASE}/recharge/billing/cancel-subscription`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ token_input: tokenInput, confirmed })
        }, '取消自动续费失败');
    },

    async resumeSubscription(tokenInput, confirmed = true) {
        return await requestJson(`${API_BASE}/recharge/billing/resume-subscription`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ token_input: tokenInput, confirmed })
        }, '恢复自动续费失败');
    },

    async getRechargeAdminOverview(options = {}) {
        return await requestJson(`${API_BASE}/recharge/admin/overview`, options, '加载充值概览失败');
    },

    async getRechargeAdminTasks(filters = {}, options = {}) {
        const parameters = new URLSearchParams(filters);
        return await requestJson(`${API_BASE}/recharge/admin/tasks?${parameters}`, options, '加载充值订单失败');
    },

    async getRechargeAdminTask(taskNo, options = {}) {
        return await requestJson(`${API_BASE}/recharge/admin/tasks/${encodeURIComponent(taskNo)}`, options, '加载订单详情失败');
    },

    async updateRechargeAdminTask(taskNo, action, payload = {}) {
        return await requestJson(`${API_BASE}/recharge/admin/tasks/${encodeURIComponent(taskNo)}/${action}`, {
            method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload)
        }, '订单操作失败');
    },

    async reconcileRechargeAdminTask(taskNo, payload, operationId) {
        const path = operationId ? `mutations/${encodeURIComponent(operationId)}/reconcile` : 'reconcile';
        return await this.updateRechargeAdminTask(taskNo, path, payload);
    },

    // 退出登录并清除服务端会话
    async logout(options = {}) {
        return await requestAuthJson(`${API_BASE}/auth/logout`, {
            signal: options.signal,
            timeoutMs: options.timeoutMs,
            method: 'POST'
        }, '退出登录失败');
    }
};

export default api;
