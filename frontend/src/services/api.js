// API 调用函数
const API_BASE = '/api';

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

const parseJsonResponse = async (res, fallbackMessage) => {
    try {
        return await res.json();
    } catch {
        throw createApiError(fallbackMessage, res.status);
    }
};

const requestJson = async (url, options, fallbackMessage) => {
    const res = await fetch(url, options);
    const data = await parseJsonResponse(res, fallbackMessage);
    if (!res.ok) {
        const error = createApiError(data.message || fallbackMessage, res.status);
        error.response = data;
        throw error;
    }
    return data;
};

const api = {
    // 登录验证（带盐值）
    async login(password) {
        const salt = generateSalt();
        const res = await fetch(`${API_BASE}/auth/login`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ password, salt })
        });
        return await parseJsonResponse(res, '登录失败');
    },

    // 检查封禁状态
    async checkAuth() {
        const res = await fetch(`${API_BASE}/auth/check`);
        return await parseJsonResponse(res, '检查登录状态失败');
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

    async getGmailMessages(connectionId, query = '') {
        const params = new URLSearchParams({ maxResults: '20' });
        if (query) params.set('q', query);
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
    // 退出登录并清除服务端会话
    async logout() {
        const res = await fetch(`${API_BASE}/auth/logout`, {
            method: 'POST'
        });
        return await parseJsonResponse(res, '退出登录失败');
    }
};

export default api;
