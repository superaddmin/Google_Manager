import { fetchWithTimeout, requestJson } from './api';

let csrfToken = '';

export const requestKey = () => Array.from(crypto.getRandomValues(new Uint8Array(16)), value => value.toString(16).padStart(2, '0')).join('');
export const setCdkCsrf = value => { csrfToken = value || ''; };

export async function cdkRequest(path, payload, options = {}) {
    const { key, ...extra } = options;
    const response = await requestJson('/api/cdk' + path, {
        ...extra,
        method: payload === undefined ? 'GET' : 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrfToken, 'Idempotency-Key': key || requestKey() },
        ...(payload === undefined ? {} : { body: JSON.stringify(payload) })
    }, '卡密服务请求失败，请保留原请求后重试');
    return response.data;
}

export async function downloadCdkExport(identifier) {
    const blob = await fetchWithTimeout('/api/cdk/admin/exports/' + encodeURIComponent(identifier) + '/download', {
        method: 'POST', credentials: 'same-origin', body: '{}',
        headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest', 'X-CSRF-Token': csrfToken }
    }, '导出下载失败', 20000, async response => {
        if (!response.ok) {
            const result = await response.json();
            throw new Error(result.message || '导出下载失败');
        }
        return response.blob();
    });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = 'platform-cdk-' + identifier + '.csv';
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export function normalizePlatformCode(value) {
    const alphabet = '0123456789ABCDEFGHJKMNPQRSTVWXYZ';
    const normalized = value.trim().toUpperCase().replace(/[- ]/g, '');
    if (!/^GM1[0-9A-HJKMNP-TV-Z]{27}$/.test(normalized)) throw new Error('请输入 GM1 开头的完整平台卡密');
    const body = normalized.slice(3, -1);
    const checksum = [...body].reduce((sum, character, index) => sum + (index + 1) * alphabet.indexOf(character), 0) % 32;
    if (alphabet[checksum] !== normalized.at(-1)) throw new Error('卡密校验位不正确，请检查输入');
    return normalized;
}
