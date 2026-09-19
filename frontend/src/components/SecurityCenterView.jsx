import React, { useState, useEffect, useRef } from 'react';
import {
    ShieldAlert,
    ShieldCheck,
    AlertTriangle,
    Lock,
    Unlock,
    RefreshCw,
    KeyRound,
    Mail,
    MailWarning,
    Copy,
    Check,
    Search,
    ExternalLink,
    Clock,
    Flame,
    Eye,
    Shield,
    Radio
} from 'lucide-react';
import api from '../services/api';

const SecurityCenterView = ({ darkMode, showNotification }) => {
    const [activeTab, setActiveTab] = useState('otps'); // 'otps' | 'forwarding' | 'accounts'
    const [overview, setOverview] = useState(null);
    const [securityAccounts, setSecurityAccounts] = useState([]);
    const [riskFilter, setRiskFilter] = useState('all');
    const [forwardingReports, setForwardingReports] = useState([]);
    const [otps, setOtps] = useState([]);
    const [otpSearch, setOtpSearch] = useState('');
    const [loading, setLoading] = useState(false);
    const [copiedKey, setCopiedKey] = useState(null);
    const isFirstMount = useRef(true);

    // 加载安全总览与风险账号
    const loadData = async () => {
        setLoading(true);
        try {
            const [overviewRes, accountsRes] = await Promise.all([
                api.getSecurityOverview(),
                api.getSecurityAccounts(riskFilter)
            ]);
            if (overviewRes.success) setOverview(overviewRes.data);
            if (accountsRes.success) setSecurityAccounts(accountsRes.data);
        } catch (error) {
            console.error('加载安全数据失败:', error);
            if (showNotification) showNotification('加载安全态势数据失败', 'error');
        } finally {
            setLoading(false);
        }
    };

    // 加载集中验证码
    const loadOtps = async () => {
        try {
            const res = await api.getCentralOTPs(20);
            if (res.success) setOtps(res.data);
        } catch (error) {
            console.error('加载验证码失败:', error);
        }
    };

    // 扫描隐蔽转发
    const loadForwardingAudit = async () => {
        try {
            const res = await api.auditForwardingRules();
            if (res.success) setForwardingReports(res.data);
        } catch (error) {
            console.error('扫描转发规则失败:', error);
        }
    };

    useEffect(() => {
        loadData();
        loadOtps();
        loadForwardingAudit();
    }, []);

    useEffect(() => {
        if (isFirstMount.current) {
            isFirstMount.current = false;
            return;
        }
        api.getSecurityAccounts(riskFilter).then(res => {
            if (res.success) setSecurityAccounts(res.data);
        }).catch(err => console.error(err));
    }, [riskFilter]);

    // 复制内容辅助（支持标准 Clipboard API 与 HTTP/非安全上下文 textarea 降级）
    const handleCopy = async (text, key) => {
        if (!text) return;
        try {
            if (navigator.clipboard && window.isSecureContext) {
                await navigator.clipboard.writeText(text);
            } else {
                const el = document.createElement('textarea');
                el.value = text;
                el.style.position = 'fixed';
                el.style.left = '-9999px';
                el.style.top = '0';
                document.body.appendChild(el);
                el.select();
                const copied = document.execCommand('copy');
                document.body.removeChild(el);
                if (!copied) throw new Error('copy command failed');
            }
            setCopiedKey(key);
            setTimeout(() => setCopiedKey(null), 2000);
            if (showNotification) showNotification(`已复制: ${text}`);
        } catch (copyErr) {
            console.error('复制失败:', copyErr);
            if (showNotification) showNotification('复制失败，请手动选择复制', 'error');
        }
    };

    // 应急锁定
    const handleLock = async (accountId, email) => {
        const confirmed = window.confirm(`【防盗应急处置】\n确定要将账号 ${email} 应急锁定吗？\n\n锁定后该账号将被标记为保护状态，修改历史中将记录安全动作。`);
        if (!confirmed) return;
        try {
            const res = await api.emergencyLockAccount(accountId, '管理员触发防盗应急锁定');
            if (res.success) {
                if (showNotification) showNotification(`账号 ${email} 已应急锁定`);
                await loadData();
            }
        } catch (error) {
            if (showNotification) showNotification(error.message || '锁定失败', 'error');
        }
    };

    // 解除锁定
    const handleUnlock = async (accountId, email) => {
        try {
            const res = await api.unlockAccount(accountId);
            if (res.success) {
                if (showNotification) showNotification(`账号 ${email} 已解除锁定`);
                await loadData();
            }
        } catch (error) {
            if (showNotification) showNotification(error.message || '解锁失败', 'error');
        }
    };

    const panelClass = darkMode ? 'bg-slate-800 border-slate-700 text-slate-100' : 'bg-white border-slate-200 text-slate-900';

    // 过滤验证码
    const filteredOtps = otps.filter(item => {
        if (!otpSearch) return true;
        const q = otpSearch.toLowerCase();
        return (
            (item.mailbox && item.mailbox.toLowerCase().includes(q)) ||
            (item.subject && item.subject.toLowerCase().includes(q)) ||
            (item.serviceName && item.serviceName.toLowerCase().includes(q)) ||
            (item.extractedOtp && item.extractedOtp.toLowerCase().includes(q))
        );
    });

    return (
        <div className="space-y-6">
            {/* 顶部标题栏 */}
            <div className="flex flex-wrap items-center justify-between gap-4">
                <div>
                    <h1 className="text-2xl font-bold flex items-center gap-2.5">
                        <ShieldAlert className="text-red-500 w-7 h-7" />
                        集中邮箱防盗与安全中控台
                    </h1>
                    <p className="text-sm opacity-70 mt-1">
                        跨邮箱验证码提取 · 隐蔽转发与黑客规则检测 · 全库安全体检 · 一键应急锁号
                    </p>
                </div>
                <div className="flex items-center gap-3">
                    <button
                        onClick={() => { loadData(); loadOtps(); loadForwardingAudit(); }}
                        disabled={loading}
                        className={`flex items-center gap-2 px-4 py-2 rounded-lg font-medium text-sm transition-all shadow-sm ${
                            darkMode ? 'bg-slate-700 hover:bg-slate-600 text-slate-200' : 'bg-slate-100 hover:bg-slate-200 text-slate-700'
                        }`}
                    >
                        <RefreshCw size={16} className={loading ? 'animate-spin' : ''} />
                        <span>重新体检</span>
                    </button>
                </div>
            </div>

            {/* 安全态势雷达卡片 */}
            {overview && (
                <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3 sm:gap-4">
                    {/* 安全指数 */}
                    <div className={`p-4 rounded-xl border ${panelClass} flex flex-col justify-between`}>
                        <div className="flex items-center justify-between text-xs opacity-70">
                            <span>全库健康指数</span>
                            <Shield className={overview.healthIndex > 70 ? 'text-green-500' : 'text-amber-500'} size={16} />
                        </div>
                        <div className="mt-2 text-2xl font-black text-blue-500">
                            {overview.healthIndex}<span className="text-xs font-normal opacity-70">/100</span>
                        </div>
                        <span className="text-[11px] opacity-60">综合评估安全度</span>
                    </div>

                    {/* 高危被盗风险 */}
                    <div className={`p-4 rounded-xl border ${panelClass} flex flex-col justify-between`}>
                        <div className="flex items-center justify-between text-xs opacity-70">
                            <span>高危被盗风险</span>
                            <Flame className="text-red-500" size={16} />
                        </div>
                        <div className="mt-2 text-2xl font-black text-red-500">
                            {overview.criticalCount}
                        </div>
                        <span className="text-[11px] text-red-400 font-medium">需立即整改补齐</span>
                    </div>

                    {/* 未配 2FA */}
                    <div className={`p-4 rounded-xl border ${panelClass} flex flex-col justify-between`}>
                        <div className="flex items-center justify-between text-xs opacity-70">
                            <span>未配 2FA 密钥</span>
                            <KeyRound className="text-amber-500" size={16} />
                        </div>
                        <div className="mt-2 text-2xl font-black text-amber-500">
                            {overview.without2FACount}
                        </div>
                        <span className="text-[11px] opacity-60">易受撞库与暴力破解</span>
                    </div>

                    {/* 缺失恢复邮箱 */}
                    <div className={`p-4 rounded-xl border ${panelClass} flex flex-col justify-between`}>
                        <div className="flex items-center justify-between text-xs opacity-70">
                            <span>缺失恢复邮箱</span>
                            <MailWarning className="text-orange-500" size={16} />
                        </div>
                        <div className="mt-2 text-2xl font-black text-orange-500">
                            {overview.withoutRecoveryCount}
                        </div>
                        <span className="text-[11px] opacity-60">无法找回密码</span>
                    </div>

                    {/* 应急锁定 */}
                    <div className={`p-4 rounded-xl border ${panelClass} flex flex-col justify-between`}>
                        <div className="flex items-center justify-between text-xs opacity-70">
                            <span>应急锁定保护</span>
                            <Lock className="text-purple-500" size={16} />
                        </div>
                        <div className="mt-2 text-2xl font-black text-purple-500">
                            {overview.lockedCount}
                        </div>
                        <span className="text-[11px] opacity-60">防盗阻断隔离</span>
                    </div>

                    {/* Gmail 托管 */}
                    <div className={`p-4 rounded-xl border ${panelClass} flex flex-col justify-between`}>
                        <div className="flex items-center justify-between text-xs opacity-70">
                            <span>已授权 Gmail</span>
                            <Radio className="text-cyan-500" size={16} />
                        </div>
                        <div className="mt-2 text-2xl font-black text-cyan-500">
                            {overview.gmailConnectionsCount}
                        </div>
                        <span className="text-[11px] opacity-60">集中 API 托管</span>
                    </div>
                </div>
            )}

            {/* 标签栏导航 */}
            <div className={`flex border-b ${darkMode ? 'border-slate-700' : 'border-slate-200'} gap-2`}>
                <button
                    onClick={() => setActiveTab('otps')}
                    className={`px-4 py-3 font-semibold text-sm border-b-2 transition-all flex items-center gap-2 ${
                        activeTab === 'otps'
                            ? 'border-blue-500 text-blue-500'
                            : 'border-transparent opacity-70 hover:opacity-100'
                    }`}
                >
                    <KeyRound size={16} />
                    集中验证码与安全告警 ({otps.length})
                </button>
                <button
                    onClick={() => setActiveTab('forwarding')}
                    className={`px-4 py-3 font-semibold text-sm border-b-2 transition-all flex items-center gap-2 ${
                        activeTab === 'forwarding'
                            ? 'border-red-500 text-red-500'
                            : 'border-transparent opacity-70 hover:opacity-100'
                    }`}
                >
                    <ShieldAlert size={16} />
                    隐蔽转发与恶意规则排查 ({forwardingReports.length})
                </button>
                <button
                    onClick={() => setActiveTab('accounts')}
                    className={`px-4 py-3 font-semibold text-sm border-b-2 transition-all flex items-center gap-2 ${
                        activeTab === 'accounts'
                            ? 'border-purple-500 text-purple-500'
                            : 'border-transparent opacity-70 hover:opacity-100'
                    }`}
                >
                    <AlertTriangle size={16} />
                    全库防盗体检与应急锁号 ({securityAccounts.length})
                </button>
            </div>

            {/* 内容区 1: 集中验证码与安全邮件 */}
            {activeTab === 'otps' && (
                <div className="space-y-4">
                    <div className={`p-4 rounded-xl border ${panelClass} flex flex-wrap gap-3 items-center justify-between`}>
                        <div className="flex-1 min-w-[280px] relative">
                            <Search className="absolute left-3 top-2.5 opacity-40" size={16} />
                            <input
                                type="text"
                                placeholder="搜索邮箱、服务名称（Google, Telegram）、或验证码..."
                                value={otpSearch}
                                onChange={e => setOtpSearch(e.target.value)}
                                className="w-full pl-9 pr-4 py-2 rounded-lg border bg-transparent text-sm"
                            />
                        </div>
                        <div className="text-xs opacity-60">
                            集中截获各邮箱最新 OTP，操作员无需直接登网页，杜绝环境风控与盗号
                        </div>
                    </div>

                    <div className={`rounded-xl border divide-y ${panelClass}`}>
                        {filteredOtps.length > 0 ? (
                            filteredOtps.map((item, index) => (
                                <div key={item.id || index} className="p-4 flex flex-col sm:flex-row sm:items-center justify-between gap-4">
                                    <div className="flex-1 min-w-0 space-y-1">
                                        <div className="flex items-center gap-2 flex-wrap">
                                            <span className="text-xs px-2 py-0.5 rounded font-bold bg-blue-500/10 text-blue-500">
                                                {item.serviceName}
                                            </span>
                                            {item.isSecurityAlert && (
                                                <span className="text-xs px-2 py-0.5 rounded font-bold bg-red-500/10 text-red-500 flex items-center gap-1">
                                                    <AlertTriangle size={12} /> 安全告警
                                                </span>
                                            )}
                                            <span className="text-sm font-semibold truncate" title={item.mailbox}>
                                                {item.mailbox}
                                            </span>
                                            <span className="text-xs opacity-50">{item.date}</span>
                                        </div>
                                        <div className="text-sm font-medium opacity-90 truncate">
                                            {item.subject || '(无主题)'}
                                        </div>
                                        <div className="text-xs opacity-60 truncate" title={item.snippet}>
                                            {item.snippet}
                                        </div>
                                    </div>

                                    {/* 验证码展示与复制区 */}
                                    <div className="flex items-center gap-2 flex-shrink-0">
                                        {item.extractedOtp ? (
                                            <div className="flex items-center gap-2 bg-emerald-500/10 border border-emerald-500/20 px-3 py-1.5 rounded-lg">
                                                <span className="text-xs opacity-70">验证码:</span>
                                                <span className="font-mono font-bold text-emerald-500 text-lg tracking-wider">
                                                    {item.extractedOtp}
                                                </span>
                                                <button
                                                    onClick={() => handleCopy(item.extractedOtp, `otp-${item.id}`)}
                                                    className="p-1 rounded hover:bg-emerald-500/20 text-emerald-500 transition-all"
                                                    title="复制验证码"
                                                >
                                                    {copiedKey === `otp-${item.id}` ? <Check size={16} /> : <Copy size={16} />}
                                                </button>
                                            </div>
                                        ) : (
                                            <span className="text-xs opacity-40 italic">未识别到纯数字码</span>
                                        )}
                                    </div>
                                </div>
                            ))
                        ) : (
                            <div className="p-12 text-center opacity-60">
                                <KeyRound size={36} className="mx-auto mb-2 opacity-30" />
                                <p>未检测到匹配的验证码邮件，或已授权 Gmail 暂无新验证码。</p>
                            </div>
                        )}
                    </div>
                </div>
            )}

            {/* 内容区 2: 隐蔽转发与恶意规则排查 */}
            {activeTab === 'forwarding' && (
                <div className="space-y-4">
                    <div className={`p-4 rounded-xl border ${panelClass} text-sm space-y-1`}>
                        <div className="font-semibold flex items-center gap-2 text-red-500">
                            <ShieldAlert size={18} />
                            黑客“隐蔽转发”防盗排查说明
                        </div>
                        <p className="opacity-70 text-xs leading-relaxed">
                            黑客侵入邮箱后，常常会在 Gmail 后台设置 <code>Auto-Forwarding</code> 或邮件过滤规则，将所有敏感邮件与验证码静默自动转发至黑客的外部邮箱，同时静默删除或归档官方安全告警。本模块通过官方 API 定期全量排查，杜绝资产被暗中窃取。
                        </p>
                    </div>

                    <div className="grid gap-4 md:grid-cols-2">
                        {forwardingReports.length > 0 ? (
                            forwardingReports.map(report => (
                                <div
                                    key={report.connectionId}
                                    className={`p-5 rounded-xl border ${panelClass} space-y-3 ${
                                        report.hasSuspiciousForwarding ? 'border-red-500 shadow-[0_0_15px_rgba(239,68,68,0.15)]' : ''
                                    }`}
                                >
                                    <div className="flex items-center justify-between">
                                        <div className="font-bold flex items-center gap-2 truncate" title={report.email}>
                                            <Mail size={16} className="text-blue-500 flex-shrink-0" />
                                            <span>{report.email}</span>
                                        </div>
                                        {report.hasSuspiciousForwarding ? (
                                            <span className="px-2 py-0.5 text-xs font-black rounded-full bg-red-500/10 text-red-500 border border-red-500/30">
                                                高危转发中
                                            </span>
                                        ) : report.isClean ? (
                                            <span className="px-2 py-0.5 text-xs font-medium rounded-full bg-green-500/10 text-green-500 border border-green-500/30">
                                                未发现隐蔽转发
                                            </span>
                                        ) : (
                                            <span className="px-2 py-0.5 text-xs font-medium rounded-full bg-amber-500/10 text-amber-500 border border-amber-500/30">
                                                存在待复核规则
                                            </span>
                                        )}
                                    </div>

                                    {/* 转发状态细节 */}
                                    <div className="text-xs space-y-1.5 opacity-80 pt-2 border-t border-slate-700/20">
                                        <div>
                                            <span className="opacity-60">自动转发地址: </span>
                                            {report.forwardingAddress ? (
                                                <span className="font-mono font-bold text-red-500">{report.forwardingAddress}</span>
                                            ) : (
                                                <span className="opacity-40">未启用</span>
                                            )}
                                        </div>
                                        <div>
                                            <span className="opacity-60">过滤器总数: </span>
                                            <span>{report.totalFilters || 0} 个</span>
                                            {report.suspiciousFiltersCount > 0 && (
                                                <span className="text-red-500 font-bold ml-2">（含 {report.suspiciousFiltersCount} 个可疑规则）</span>
                                            )}
                                        </div>
                                        <div>
                                            <span className="opacity-60">POP/IMAP: </span>
                                            <span>IMAP: {report.imapEnabled ? '开启' : '关闭'} · POP: {report.popStatus || 'disabled'}</span>
                                        </div>
                                    </div>

                                    {/* 发现的高危项 */}
                                    {report.findings && report.findings.length > 0 && (
                                        <div className="mt-3 p-3 rounded-lg bg-red-500/10 border border-red-500/20 text-xs space-y-1">
                                            <div className="font-bold text-red-500">检测到安全风险项:</div>
                                            {report.findings.map((f, i) => (
                                                <div key={i} className="text-red-400">
                                                    • {f.title}: {f.description}
                                                </div>
                                            ))}
                                        </div>
                                    )}
                                </div>
                            ))
                        ) : (
                            <div className="col-span-2 p-12 text-center opacity-60">
                                <ShieldCheck size={36} className="mx-auto mb-2 opacity-30" />
                                <p>尚未授权 Gmail 或未发现隐蔽转发规则。</p>
                            </div>
                        )}
                    </div>
                </div>
            )}

            {/* 内容区 3: 全库防盗体检与应急锁号 */}
            {activeTab === 'accounts' && (
                <div className="space-y-4">
                    {/* 筛选切换 */}
                    <div className="flex flex-wrap gap-2 items-center justify-between">
                        <div className="flex gap-1 p-1 rounded-xl bg-slate-500/10">
                            {[
                                { id: 'all', label: '全部账号' },
                                { id: 'critical', label: '高危被盗风险' },
                                { id: 'warning', label: '中度风险' },
                                { id: 'locked', label: '已应急锁定' },
                            ].map(item => (
                                <button
                                    key={item.id}
                                    onClick={() => setRiskFilter(item.id)}
                                    className={`px-3 py-1.5 text-xs font-semibold rounded-lg transition-all ${
                                        riskFilter === item.id
                                            ? 'bg-blue-600 text-white shadow-sm'
                                            : 'opacity-70 hover:opacity-100'
                                    }`}
                                >
                                    {item.label}
                                </button>
                            ))}
                        </div>
                        <div className="text-xs opacity-60">
                            对异常或疑似泄露的账号提供一键应急锁定，阻止导出
                        </div>
                    </div>

                    {/* 风险账号表格 */}
                    <div className={`rounded-xl border overflow-hidden ${panelClass}`}>
                        <table className="w-full text-left text-sm">
                            <thead className={`border-b text-xs uppercase ${darkMode ? 'border-slate-700 bg-slate-800/60' : 'border-slate-200 bg-slate-50'}`}>
                                <tr>
                                    <th className="px-4 py-3">邮箱账号</th>
                                    <th className="px-4 py-3">风险评级</th>
                                    <th className="px-4 py-3">2FA 保护</th>
                                    <th className="px-4 py-3">安全恢复邮箱</th>
                                    <th className="px-4 py-3">风险排查原因</th>
                                    <th className="px-4 py-3 text-right">防盗应急操作</th>
                                </tr>
                            </thead>
                            <tbody className="divide-y divide-slate-700/20">
                                {securityAccounts.length > 0 ? (
                                    securityAccounts.map(acc => (
                                        <tr key={acc.accountId} className="hover:bg-slate-500/5 transition-colors">
                                            <td className="px-4 py-3 font-medium">
                                                <div className="flex items-center gap-2">
                                                    <span>{acc.email}</span>
                                                    {acc.isLocked && (
                                                        <span className="px-1.5 py-0.5 text-[10px] font-bold rounded bg-purple-500/10 text-purple-500 border border-purple-500/30">
                                                            LOCKED
                                                        </span>
                                                    )}
                                                </div>
                                            </td>
                                            <td className="px-4 py-3">
                                                {acc.riskLevel === 'critical' ? (
                                                    <span className="px-2 py-0.5 text-xs font-bold rounded-full bg-red-500/10 text-red-500 border border-red-500/30">
                                                        高危 ({acc.riskScore}分)
                                                    </span>
                                                ) : acc.riskLevel === 'warning' ? (
                                                    <span className="px-2 py-0.5 text-xs font-bold rounded-full bg-amber-500/10 text-amber-500 border border-amber-500/30">
                                                        中度 ({acc.riskScore}分)
                                                    </span>
                                                ) : acc.riskLevel === 'locked' ? (
                                                    <span className="px-2 py-0.5 text-xs font-bold rounded-full bg-purple-500/10 text-purple-500 border border-purple-500/30">
                                                        已锁定保护
                                                    </span>
                                                ) : (
                                                    <span className="px-2 py-0.5 text-xs font-bold rounded-full bg-green-500/10 text-green-500 border border-green-500/30">
                                                        安全
                                                    </span>
                                                )}
                                            </td>
                                            <td className="px-4 py-3">
                                                {acc.has2FA ? (
                                                    <span className="text-green-500 text-xs font-semibold flex items-center gap-1">
                                                        <Check size={14} /> 已配置
                                                    </span>
                                                ) : (
                                                    <span className="text-red-500 text-xs font-bold flex items-center gap-1">
                                                        <AlertTriangle size={14} /> 缺失 2FA
                                                    </span>
                                                )}
                                            </td>
                                            <td className="px-4 py-3">
                                                {acc.hasRecovery ? (
                                                    <span className="text-green-500 text-xs font-semibold flex items-center gap-1">
                                                        <Check size={14} /> 已配置
                                                    </span>
                                                ) : (
                                                    <span className="text-amber-500 text-xs font-bold flex items-center gap-1">
                                                        <AlertTriangle size={14} /> 缺失
                                                    </span>
                                                )}
                                            </td>
                                            <td className="px-4 py-3 max-w-xs text-xs opacity-70 truncate">
                                                {acc.riskFactors && acc.riskFactors.length > 0 ? (
                                                    acc.riskFactors.join('；')
                                                ) : (
                                                    '各项指标正常'
                                                )}
                                            </td>
                                            <td className="px-4 py-3 text-right">
                                                {acc.isLocked ? (
                                                    <button
                                                        onClick={() => handleUnlock(acc.accountId, acc.email)}
                                                        className="px-3 py-1 rounded-lg text-xs font-bold bg-green-500/10 text-green-500 border border-green-500/30 hover:bg-green-500/20 transition-all"
                                                    >
                                                        <Unlock size={13} className="inline mr-1" />
                                                        解除锁定
                                                    </button>
                                                ) : (
                                                    <button
                                                        onClick={() => handleLock(acc.accountId, acc.email)}
                                                        className="px-3 py-1 rounded-lg text-xs font-bold bg-red-500/10 text-red-500 border border-red-500/30 hover:bg-red-500/20 transition-all"
                                                    >
                                                        <Lock size={13} className="inline mr-1" />
                                                        应急锁定
                                                    </button>
                                                )}
                                            </td>
                                        </tr>
                                    ))
                                ) : (
                                    <tr>
                                        <td colSpan="6" className="p-8 text-center opacity-60">
                                            暂无匹配该风险级别的账号
                                        </td>
                                    </tr>
                                )}
                            </tbody>
                        </table>
                    </div>
                </div>
            )}
        </div>
    );
};

export default SecurityCenterView;
