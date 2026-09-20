import React, { useEffect, useState, useRef } from 'react';
import {
    Archive,
    Check,
    ExternalLink,
    Mail,
    RefreshCw,
    Play,
    Square,
    Zap,
    Key,
    Shield,
    Clock,
    Settings,
    X,
    ChevronDown,
    ChevronUp,
} from 'lucide-react';
import api from '../services/api';

const GmailInboxView = ({ darkMode }) => {
    const [connections, setConnections] = useState([]);
    const [connectionId, setConnectionId] = useState('');
    const [messages, setMessages] = useState([]);
    const [nextPageToken, setNextPageToken] = useState('');
    const [selected, setSelected] = useState(null);
    const [query, setQuery] = useState('');
    const [loading, setLoading] = useState(false);
    const [authorizing, setAuthorizing] = useState(false);
    const [error, setError] = useState('');

    // 挂机守护进程状态 (按需展开与加载，避免初次挂载产生非预期请求)
    const [showDaemonPanel, setShowDaemonPanel] = useState(false);
    const [daemonStatus, setDaemonStatus] = useState(null);
    const [daemonLoading, setDaemonLoading] = useState(false);
    const [syncNotice, setSyncNotice] = useState('');
    const [syncInterval, setSyncInterval] = useState(180);

    // 批量授权弹窗与任务状态
    const [showBatchModal, setShowBatchModal] = useState(false);
    const [allAccounts, setAllAccounts] = useState([]);
    const [selectedAccountIds, setSelectedAccountIds] = useState([]);
    const [batchOptions, setBatchOptions] = useState({
        headless: true,
        slowMo: 150,
        accountDelay: 3000,
        proxy: '',
    });
    const [batchTask, setBatchTask] = useState(null);
    const [batchLoading, setBatchLoading] = useState(false);
    const [batchMessage, setBatchMessage] = useState('');

    const logEndRef = useRef(null);
    const connectionIdRef = useRef('');
    const messagesRequestIdRef = useRef(0);
    const messageDetailRequestIdRef = useRef(0);

    const changeConnection = (nextConnectionId) => {
        const normalizedId = String(nextConnectionId || '');
        connectionIdRef.current = normalizedId;
        messagesRequestIdRef.current += 1;
        messageDetailRequestIdRef.current += 1;
        setConnectionId(normalizedId);
        setMessages([]);
        setNextPageToken('');
        setSelected(null);
        setLoading(false);
        setError('');
    };

    const changeQuery = (nextQuery) => {
        messagesRequestIdRef.current += 1;
        messageDetailRequestIdRef.current += 1;
        setQuery(nextQuery);
        setMessages([]);
        setNextPageToken('');
        setSelected(null);
        setLoading(false);
        setError('');
    };

    const loadConnections = async () => {
        try {
            const result = await api.getGmailConnections();
            const list = result.data || [];
            setConnections(list);
            if (!connectionIdRef.current && list.length) {
                changeConnection(list[0].id);
            }
        } catch (err) {
            setError(err.message || '加载 Gmail 连接失败');
        }
    };

    const loadMessages = async ({ append = false, pageToken = '', searchQuery = query } = {}) => {
        const ownerConnectionId = connectionIdRef.current;
        if (!ownerConnectionId) return;
        const requestId = ++messagesRequestIdRef.current;
        if (!append) {
            messageDetailRequestIdRef.current += 1;
            setMessages([]);
            setNextPageToken('');
            setSelected(null);
        }
        setLoading(true);
        setError('');
        try {
            const result = await api.getGmailMessages(ownerConnectionId, searchQuery, pageToken);
            if (messagesRequestIdRef.current !== requestId || connectionIdRef.current !== ownerConnectionId) return;
            const pageMessages = result.data?.messages || [];
            setMessages(currentMessages => {
                if (!append) return pageMessages;
                const knownIds = new Set(currentMessages.map(message => message.id));
                return [...currentMessages, ...pageMessages.filter(message => !knownIds.has(message.id))];
            });
            setNextPageToken(result.data?.nextPageToken || result.data?.next_page_token || '');
        } catch (loadError) {
            if (messagesRequestIdRef.current === requestId && connectionIdRef.current === ownerConnectionId) {
                setError(loadError.message || '加载 Gmail 收件箱失败');
            }
        } finally {
            if (messagesRequestIdRef.current === requestId && connectionIdRef.current === ownerConnectionId) {
                setLoading(false);
            }
        }
    };

    const loadDaemonStatus = async () => {
        try {
            const res = await api.getGmailDaemonStatus();
            setDaemonStatus(res.data);
            if (res.data?.intervalSeconds) {
                setSyncInterval(res.data.intervalSeconds);
            }
        } catch {
            // ignore
        }
    };

    const loadBatchStatus = async () => {
        try {
            const res = await api.getBatchOAuthStatus();
            if (res.data?.active) {
                setBatchTask(res.data.active);
            } else if (res.data?.latest) {
                setBatchTask(res.data.latest);
            }
        } catch {
            // ignore
        }
    };

    // 仅在挂载时加载连接列表（遵循原有单测路由契约）
    useEffect(() => {
        loadConnections().catch(loadError => setError(loadError.message));
        return () => {
            messagesRequestIdRef.current += 1;
            messageDetailRequestIdRef.current += 1;
        };
    }, []);

    useEffect(() => {
        loadMessages({ searchQuery: query });
    }, [connectionId]);

    // 仅在展开挂机面板后开启定期轮询
    useEffect(() => {
        if (!showDaemonPanel) return;
        loadDaemonStatus();
        const timer = setInterval(() => {
            loadDaemonStatus();
        }, 8000);
        return () => clearInterval(timer);
    }, [showDaemonPanel]);

    // 批量授权任务运行中时每 2s 轮询
    useEffect(() => {
        let timer = null;
        if (showBatchModal && batchTask && (batchTask.status === 'running' || batchTask.status === 'pending')) {
            timer = setInterval(async () => {
                const res = await api.getBatchOAuthStatus().catch(() => null);
                if (res?.data?.active) {
                    setBatchTask(res.data.active);
                } else if (res?.data?.latest) {
                    setBatchTask(res.data.latest);
                    loadConnections();
                }
            }, 2000);
        }
        return () => {
            if (timer) clearInterval(timer);
        };
    }, [showBatchModal, batchTask?.status]);

    useEffect(() => {
        if (logEndRef.current) {
            logEndRef.current.scrollIntoView({ behavior: 'smooth' });
        }
    }, [batchTask?.logs]);

    // 挂机守护启停
    const toggleDaemon = async () => {
        setDaemonLoading(true);
        setError('');
        try {
            if (daemonStatus?.isRunning) {
                const res = await api.stopGmailDaemon();
                setDaemonStatus(res.data);
            } else {
                const res = await api.startGmailDaemon(syncInterval);
                setDaemonStatus(res.data);
            }
        } catch (err) {
            setError(err.message || '操作挂机守护进程失败');
        } finally {
            setDaemonLoading(false);
        }
    };

    const syncNow = async () => {
        setDaemonLoading(true);
        setError('');
        setSyncNotice('');
        try {
            const result = await api.syncGmailDaemonNow();
            if (result.data?.status === 'pending' || result.data?.status === 'queued') {
                setSyncNotice(result.message || '同步任务已排队');
                await loadDaemonStatus();
                return;
            }
            await loadDaemonStatus();
            await loadMessages();
        } catch (err) {
            setError(err.message || '全量同步失败');
        } finally {
            setDaemonLoading(false);
        }
    };

    // 单账号手动 OAuth
    const authorize = async () => {
        if (authorizing) return;
        setAuthorizing(true);
        setError('');
        try {
            const result = await api.startGmailOAuth();
            const authorizationUrl = result.data?.authorizationUrl;
            if (!authorizationUrl) throw new Error('Gmail 授权地址为空');
            window.location.href = authorizationUrl;
        } catch (authorizeError) {
            setError(authorizeError.message || '启动 Gmail 授权失败');
        } finally {
            setAuthorizing(false);
        }
    };

    // 打开批量授权模态框
    const openBatchModal = async () => {
        setShowBatchModal(true);
        setBatchMessage('');
        loadBatchStatus();
        try {
            const res = await api.getAccounts();
            const accounts = Array.isArray(res) ? res : (res?.data || []);
            setAllAccounts(accounts);

            const connectedEmails = new Set(connections.map(c => c.email.toLowerCase()));
            const unlinked = accounts.filter(a => !connectedEmails.has(a.email.toLowerCase()));
            setSelectedAccountIds(unlinked.map(a => a.id));
        } catch (err) {
            setBatchMessage(`加载账号失败: ${err.message}`);
        }
    };

    const startBatchAuth = async () => {
        if (!selectedAccountIds.length) {
            setBatchMessage('请先至少勾选一个待授权账号');
            return;
        }
        setBatchLoading(true);
        setBatchMessage('');
        try {
            const res = await api.startBatchOAuth(selectedAccountIds, batchOptions);
            setBatchTask(res.data);
            setBatchMessage('批量授权任务已启动！正在后台驱动 Playwright 自动登录授权...');
        } catch (err) {
            setBatchMessage(`启动失败: ${err.message}`);
        } finally {
            setBatchLoading(false);
        }
    };

    const cancelBatchAuth = async () => {
        if (!batchTask?.taskId) return;
        try {
            await api.cancelBatchOAuth(batchTask.taskId);
            await loadBatchStatus();
        } catch (err) {
            setBatchMessage(`取消失败: ${err.message}`);
        }
    };

    const openMessage = async (message) => {
        const ownerConnectionId = connectionIdRef.current;
        const requestId = ++messageDetailRequestIdRef.current;
        setError('');
        try {
            const result = await api.getGmailMessage(ownerConnectionId, message.id);
            if (messageDetailRequestIdRef.current !== requestId || connectionIdRef.current !== ownerConnectionId) return;
            setSelected(result.data);
        } catch (detailError) {
            if (messageDetailRequestIdRef.current === requestId && connectionIdRef.current === ownerConnectionId) {
                setError(detailError.message || '加载 Gmail 邮件失败');
            }
        }
    };

    const modify = async (message, action) => {
        const ownerConnectionId = connectionIdRef.current;
        setError('');
        try {
            if (action === 'read') await api.markGmailMessageRead(ownerConnectionId, message.id);
            if (action === 'archive') await api.archiveGmailMessage(ownerConnectionId, message.id);
            if (connectionIdRef.current !== ownerConnectionId) return;
            await loadMessages({ searchQuery: query });
        } catch (modifyError) {
            if (connectionIdRef.current === ownerConnectionId) {
                setError(modifyError.message || (action === 'archive' ? '归档邮件失败' : '标记邮件失败'));
            }
        }
    };

    const panel = darkMode
        ? 'bg-slate-800 border-slate-700 text-slate-100'
        : 'bg-white border-slate-200 text-slate-900';

    const connectedEmails = new Set(connections.map(c => c.email.toLowerCase()));
    const unlinkedAccounts = allAccounts.filter(a => !connectedEmails.has(a.email.toLowerCase()));

    return (
        <div className="space-y-6">
            {/* 顶栏标题与操作区 */}
            <div className="flex flex-wrap items-center justify-between gap-3">
                <div>
                    <h1 className="text-2xl font-bold">Gmail 收件箱</h1>
                    <p className="text-sm opacity-70">
                        通过 Gmail API 管理已授权邮箱与 24H 挂机收信
                    </p>
                </div>
                <div className="flex flex-wrap gap-2">
                    <button
                        onClick={() => {
                            setShowDaemonPanel(!showDaemonPanel);
                            if (!showDaemonPanel) loadDaemonStatus();
                        }}
                        className={`px-3 py-2 rounded-lg border text-sm font-medium flex items-center gap-1.5 transition ${panel} hover:bg-slate-500/10`}
                    >
                        <Shield size={16} className={daemonStatus?.isRunning ? 'text-emerald-500' : 'text-slate-400'} />
                        <span>挂机收信中控</span>
                        {showDaemonPanel ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
                    </button>
                    <button
                        onClick={openBatchModal}
                        className="px-4 py-2 rounded-lg bg-indigo-600 hover:bg-indigo-700 text-white text-sm font-medium flex items-center gap-1.5 shadow-sm transition"
                    >
                        <Zap size={16} />
                        批量自动授权
                    </button>
                    <button
                        onClick={authorize}
                        disabled={authorizing}
                        className="px-4 py-2 rounded-lg bg-blue-600 hover:bg-blue-700 text-white text-sm font-medium flex items-center gap-1.5 disabled:opacity-60 transition"
                    >
                        <ExternalLink size={16} />
                        {authorizing ? '授权中...' : '授权 Gmail'}
                    </button>
                    <button
                        onClick={() => {
                            loadConnections();
                            loadMessages();
                            if (showDaemonPanel) loadDaemonStatus();
                        }}
                        className={`p-2.5 rounded-lg border ${panel} hover:bg-slate-500/10 transition`}
                        title="刷新"
                    >
                        <RefreshCw size={16} />
                    </button>
                </div>
            </div>

            {/* 挂机收信守护进程中控栏（可展开/折叠） */}
            {showDaemonPanel && (
                <div className={`rounded-xl border p-4 shadow-sm animate-fade-in ${panel}`}>
                    <div className="flex flex-wrap items-center justify-between gap-4">
                        <div className="flex items-center gap-3">
                            <div className={`p-2.5 rounded-xl ${daemonStatus?.isRunning ? 'bg-emerald-500/10 text-emerald-500' : 'bg-slate-500/10 text-slate-400'}`}>
                                <Shield size={22} />
                            </div>
                            <div>
                                <div className="flex items-center gap-2">
                                    <span className="font-semibold text-base">24H 挂机收信守护者</span>
                                    <span className={`text-xs px-2 py-0.5 rounded font-medium ${daemonStatus?.isRunning ? 'bg-emerald-500/10 text-emerald-500' : 'bg-slate-500/10 text-slate-400'}`}>
                                        {daemonStatus?.isRunning ? '服务运行中' : '未开启'}
                                    </span>
                                </div>
                                <p className="text-xs opacity-60 mt-0.5">
                                    后台自动调用 Gmail REST API 批量轮询未读邮件、归集验证码与检测可疑转发
                                </p>
                            </div>
                        </div>

                        <div className="flex flex-wrap items-center gap-3">
                            <div className="flex items-center gap-2 text-xs opacity-80">
                                <Clock size={14} />
                                <span>轮询周期:</span>
                                <select
                                    value={syncInterval}
                                    onChange={e => setSyncInterval(Number(e.target.value))}
                                    disabled={daemonStatus?.isRunning}
                                    className="rounded border px-2 py-1 bg-transparent text-xs"
                                >
                                    <option value="60">1 分钟</option>
                                    <option value="180">3 分钟 (推荐)</option>
                                    <option value="300">5 分钟</option>
                                    <option value="600">10 分钟</option>
                                </select>
                            </div>

                            <button
                                onClick={toggleDaemon}
                                disabled={daemonLoading}
                                className={`px-3.5 py-1.5 rounded-lg font-medium text-xs flex items-center gap-1.5 transition ${
                                    daemonStatus?.isRunning
                                        ? 'bg-amber-600 hover:bg-amber-700 text-white'
                                        : 'bg-emerald-600 hover:bg-emerald-700 text-white'
                                }`}
                            >
                                {daemonStatus?.isRunning ? (
                                    <>
                                        <Square size={14} /> 停止挂机
                                    </>
                                ) : (
                                    <>
                                        <Play size={14} /> 开启挂机收信
                                    </>
                                )}
                            </button>

                            <button
                                onClick={syncNow}
                                disabled={daemonLoading}
                                className="px-3 py-1.5 rounded-lg border text-xs font-medium flex items-center gap-1 hover:bg-slate-500/10 transition"
                            >
                                <RefreshCw size={14} className={daemonLoading ? 'animate-spin' : ''} />
                                立即拉取
                            </button>
                        </div>
                    </div>

                    {syncNotice && (
                        <div className="mt-3 p-3 rounded-lg border border-blue-500/30 bg-blue-500/10 text-blue-500 text-xs">
                            {syncNotice}
                        </div>
                    )}

                    {/* 指标条 */}
                    {daemonStatus && (
                        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mt-4 pt-3 border-t border-slate-500/20 text-xs">
                            <div>
                                <span className="opacity-60">已授权邮箱数:</span>{' '}
                                <span className="font-semibold">{connections.length}</span>
                            </div>
                            <div>
                                <span className="opacity-60">已累计轮询:</span>{' '}
                                <span className="font-semibold">{daemonStatus.totalRuns || 0} 次</span>
                            </div>
                            <div>
                                <span className="opacity-60">抓取邮件总数:</span>{' '}
                                <span className="font-semibold text-blue-500">{daemonStatus.messagesPolled || 0}</span>
                            </div>
                            <div>
                                <span className="opacity-60">最近执行耗时:</span>{' '}
                                <span className="font-semibold">{daemonStatus.lastDurationSeconds || 0}s</span>
                            </div>
                        </div>
                    )}
                </div>
            )}

            {/* 邮箱选择与搜索框 */}
            <div className={`rounded-xl border p-4 shadow-sm ${panel}`}>
                <div className="flex flex-wrap gap-3">
                    <select
                        value={connectionId}
                        onChange={event => changeConnection(event.target.value)}
                        className={`rounded-lg border px-3 py-2 text-sm font-medium transition focus:outline-none focus:ring-2 focus:ring-indigo-500/50 min-w-[220px] ${
                            darkMode
                                ? 'bg-slate-800/80 border-slate-700 text-slate-200'
                                : 'bg-white border-slate-200 text-slate-700'
                        }`}
                    >
                        <option value="" className={darkMode ? 'bg-slate-800 text-slate-300' : 'bg-white text-slate-700'}>
                            选择已授权邮箱
                        </option>
                        {connections.map(item => (
                            <option key={item.id} value={item.id} className={darkMode ? 'bg-slate-800 text-slate-200' : 'bg-white text-slate-800'}>
                                {item.email}
                            </option>
                        ))}
                    </select>
                    <input
                        value={query}
                        onChange={event => changeQuery(event.target.value)}
                        onKeyDown={event => event.key === 'Enter' && loadMessages({ searchQuery: query })}
                        placeholder="搜索 Gmail，例如 from:github.com"
                        className={`flex-1 min-w-[240px] rounded-lg border px-3 py-2 text-sm transition focus:outline-none focus:ring-2 focus:ring-indigo-500/50 ${
                            darkMode
                                ? 'bg-slate-800/80 border-slate-700 text-slate-100 placeholder-slate-500'
                                : 'bg-white border-slate-200 text-slate-800 placeholder-slate-400'
                        }`}
                    />
                    <button
                        onClick={() => loadMessages({ searchQuery: query })}
                        disabled={!connectionId || loading}
                        className="px-5 py-2 rounded-lg bg-indigo-600 hover:bg-indigo-700 disabled:opacity-50 text-white text-sm font-medium shadow-sm transition"
                    >
                        {loading ? '搜索中...' : '搜索'}
                    </button>
                </div>
                {error && <p className="mt-3 text-red-500 text-sm">{error}</p>}
            </div>

            {/* 未授权提示 */}
            {!connections.length && (
                <div className={`rounded-xl border p-8 text-center ${panel}`}>
                    <Mail className="mx-auto mb-3 opacity-40" size={36} />
                    <p className="text-sm">尚未授权 Gmail 账号，请先点击“授权 Gmail”。</p>
                </div>
            )}

            {/* 邮件左右双栏分栏 */}
            {connections.length > 0 && (
                <div className="grid gap-6 lg:grid-cols-[1fr_1fr]">
                    {/* 左侧邮件列表 */}
                    <div className={`rounded-xl border divide-y overflow-hidden shadow-sm ${panel}`}>
                        <div className="p-3 bg-slate-500/5 font-medium text-xs opacity-70 flex justify-between">
                            <span>邮件列表 ({messages.length})</span>
                            <span>{query ? `过滤: ${query}` : '收件箱'}</span>
                        </div>
                        <div className="max-h-[600px] overflow-y-auto divide-y divide-slate-500/10">
                            {messages.map(message => (
                                <div
                                    key={message.id}
                                    className={`p-3.5 flex gap-3 items-start transition hover:bg-slate-500/5 ${
                                        selected?.id === message.id ? 'bg-blue-500/10' : ''
                                    }`}
                                >
                                    <button
                                        className="text-left flex-1 min-w-0"
                                        onClick={() => openMessage(message)}
                                    >
                                        <div className="font-semibold text-sm truncate">
                                            {message.subject || '(无主题)'}
                                        </div>
                                        <div className="text-xs opacity-70 truncate mt-0.5">
                                            {message.from}
                                        </div>
                                        <div className="text-xs opacity-60 truncate mt-1">
                                            {message.snippet}
                                        </div>
                                    </button>
                                    <div className="flex gap-1">
                                        <button
                                            title="标记已读"
                                            onClick={() => modify(message, 'read')}
                                            className="p-1.5 rounded hover:bg-slate-500/20 text-slate-400 hover:text-emerald-500 transition"
                                        >
                                            <Check size={16} />
                                        </button>
                                        <button
                                            title="归档"
                                            onClick={() => modify(message, 'archive')}
                                            className="p-1.5 rounded hover:bg-slate-500/20 text-slate-400 hover:text-amber-500 transition"
                                        >
                                            <Archive size={16} />
                                        </button>
                                    </div>
                                </div>
                            ))}
                            {nextPageToken && (
                                <div className="p-3 text-center">
                                    <button
                                        type="button"
                                        onClick={() => loadMessages({ append: true, pageToken: nextPageToken, searchQuery: query })}
                                        disabled={loading}
                                        className="px-4 py-2 rounded-lg border text-xs font-medium hover:bg-slate-500/10 disabled:opacity-50 transition"
                                    >
                                        {loading ? '加载中...' : '加载更多'}
                                    </button>
                                </div>
                            )}
                            {!loading && connectionId && !messages.length && (
                                <p className="p-8 text-center text-sm opacity-70">收件箱暂无匹配邮件</p>
                            )}
                        </div>
                    </div>

                    {/* 右侧邮件详情 */}
                    <div className={`rounded-xl border p-5 min-h-[240px] shadow-sm ${panel}`}>
                        {selected ? (
                            <div className="space-y-4">
                                <div>
                                    <h2 className="text-xl font-bold leading-snug">
                                        {selected.subject || '(无主题)'}
                                    </h2>
                                    <div className="mt-2 text-xs opacity-70 flex flex-wrap gap-x-4 gap-y-1">
                                        <span>发件人: {selected.from}</span>
                                        <span>时间: {selected.date}</span>
                                    </div>
                                </div>
                                <hr className="border-slate-500/20" />
                                <pre className="whitespace-pre-wrap text-sm font-sans leading-relaxed break-words max-h-[500px] overflow-y-auto">
                                    {selected.body || selected.snippet}
                                </pre>
                            </div>
                        ) : (
                            <p className="text-center opacity-60 mt-16">选择邮件查看详情</p>
                        )}
                    </div>
                </div>
            )}

            {/* 批量 OAuth 自动授权模态框 */}
            {showBatchModal && (
                <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/60 backdrop-blur-sm animate-fade-in">
                    <div
                        className={`w-full max-w-2xl rounded-2xl border shadow-2xl overflow-hidden max-h-[90vh] flex flex-col ${panel}`}
                    >
                        {/* 头部 */}
                        <div className="p-5 border-b border-slate-500/20 flex items-center justify-between">
                            <div className="flex items-center gap-2.5">
                                <div className="p-2 rounded-xl bg-indigo-500/10 text-indigo-500">
                                    <Zap size={20} />
                                </div>
                                <div>
                                    <h3 className="font-bold text-lg">Playwright 批量全自动挂机授权</h3>
                                    <p className="text-xs opacity-60">
                                        自动驱动无头浏览器填密、TOTP 验证、绕过应用警告并授权 Gmail API
                                    </p>
                                </div>
                            </div>
                            <button
                                onClick={() => setShowBatchModal(false)}
                                className="p-1.5 rounded-lg hover:bg-slate-500/20 opacity-70 hover:opacity-100 transition"
                            >
                                <X size={20} />
                            </button>
                        </div>

                        {/* 主体 */}
                        <div className="p-5 overflow-y-auto space-y-5 flex-1">
                            {batchMessage && (
                                <div className="p-3 rounded-lg bg-indigo-500/10 border border-indigo-500/30 text-xs text-indigo-400">
                                    {batchMessage}
                                </div>
                            )}

                            {/* 任务进度卡片 */}
                            {batchTask && (
                                <div className="p-4 rounded-xl border border-slate-500/20 bg-slate-500/5 space-y-3">
                                    <div className="flex items-center justify-between">
                                        <span className="font-semibold text-sm flex items-center gap-2">
                                            任务状态:
                                            <span
                                                className={`text-xs px-2 py-0.5 rounded font-medium ${
                                                    batchTask.status === 'running'
                                                        ? 'bg-blue-500/20 text-blue-400 animate-pulse'
                                                        : batchTask.status === 'completed'
                                                        ? 'bg-emerald-500/20 text-emerald-400'
                                                        : 'bg-amber-500/20 text-amber-400'
                                                }`}
                                            >
                                                {batchTask.status === 'running'
                                                    ? '自动授权进行中'
                                                    : batchTask.status === 'completed'
                                                    ? '已完成'
                                                    : batchTask.status}
                                            </span>
                                        </span>
                                        <span className="text-xs opacity-70">
                                            已完成 {batchTask.completedCount || 0} / 失败 {batchTask.failedCount || 0} / 总计 {batchTask.totalCount || 0}
                                        </span>
                                    </div>

                                    {/* 进度条 */}
                                    <div className="w-full bg-slate-500/20 h-2.5 rounded-full overflow-hidden">
                                        <div
                                            className="bg-indigo-500 h-full transition-all duration-300"
                                            style={{
                                                width: `${
                                                    batchTask.totalCount > 0
                                                        ? Math.round(
                                                              (((batchTask.completedCount || 0) + (batchTask.failedCount || 0)) /
                                                                  batchTask.totalCount) *
                                                                  100
                                                          )
                                                        : 0
                                                }%`,
                                            }}
                                        ></div>
                                    </div>

                                    {/* 当前账号 */}
                                    {batchTask.currentEmail && (
                                        <div className="text-xs opacity-80 flex items-center gap-1.5">
                                            <Key size={13} className="text-indigo-400" />
                                            <span>当前处理: {batchTask.currentEmail}</span>
                                        </div>
                                    )}

                                    {/* 实时滚动日志 */}
                                    {batchTask.logs?.length > 0 && (
                                        <div className="bg-slate-950/80 rounded-lg p-3 max-h-36 overflow-y-auto text-[11px] font-mono text-emerald-400 space-y-1">
                                            {batchTask.logs.map((log, idx) => (
                                                <div key={idx}>
                                                    <span className="opacity-50">[{log.time}]</span> {log.message}
                                                </div>
                                            ))}
                                            <div ref={logEndRef} />
                                        </div>
                                    )}

                                    {batchTask.status === 'running' && (
                                        <div className="text-right pt-1">
                                            <button
                                                onClick={cancelBatchAuth}
                                                className="px-3 py-1 rounded bg-red-600/80 hover:bg-red-700 text-white text-xs"
                                            >
                                                取消本次任务
                                            </button>
                                        </div>
                                    )}
                                </div>
                            )}

                            {/* 待选账号列表 */}
                            <div>
                                <div className="flex items-center justify-between mb-2">
                                    <label className="text-xs font-semibold uppercase opacity-70">
                                        勾选待授权账号 (已筛选未绑定邮箱: {unlinkedAccounts.length})
                                    </label>
                                    <div className="flex gap-2 text-xs">
                                        <button
                                            type="button"
                                            onClick={() => setSelectedAccountIds(unlinkedAccounts.map(a => a.id))}
                                            className="text-indigo-400 hover:underline"
                                        >
                                            全选未授权
                                        </button>
                                        <span>·</span>
                                        <button
                                            type="button"
                                            onClick={() => setSelectedAccountIds([])}
                                            className="text-slate-400 hover:underline"
                                        >
                                            清空勾选
                                        </button>
                                    </div>
                                </div>

                                <div className="border border-slate-500/20 rounded-xl max-h-48 overflow-y-auto divide-y divide-slate-500/10 p-1">
                                    {unlinkedAccounts.length === 0 ? (
                                        <p className="p-6 text-center text-xs opacity-50">
                                            全部账号均已成功授权 Gmail API，无需重复操作。
                                        </p>
                                    ) : (
                                        unlinkedAccounts.map(acc => (
                                            <label
                                                key={acc.id}
                                                className="flex items-center gap-3 p-2 text-xs hover:bg-slate-500/5 cursor-pointer rounded"
                                            >
                                                <input
                                                    type="checkbox"
                                                    checked={selectedAccountIds.includes(acc.id)}
                                                    onChange={e => {
                                                        if (e.target.checked) {
                                                            setSelectedAccountIds([...selectedAccountIds, acc.id]);
                                                        } else {
                                                            setSelectedAccountIds(
                                                                selectedAccountIds.filter(id => id !== acc.id)
                                                            );
                                                        }
                                                    }}
                                                    className="rounded border-slate-500 text-indigo-600 focus:ring-indigo-500"
                                                />
                                                <span className="font-medium flex-1 truncate">{acc.email}</span>
                                                <span className="opacity-50 text-[11px]">
                                                    {acc.secret ? '含 2FA' : '无 2FA'} · {acc.recovery ? '含恢复邮箱' : '无恢复邮箱'}
                                                </span>
                                            </label>
                                        ))
                                    )}
                                </div>
                            </div>

                            {/* 运行参数配置 */}
                            <div className="border border-slate-500/20 rounded-xl p-3.5 space-y-3 bg-slate-500/5">
                                <div className="text-xs font-semibold flex items-center gap-1.5 opacity-80">
                                    <Settings size={14} /> 运行与网络选项
                                </div>
                                <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 text-xs">
                                    <label className="flex items-center gap-2 cursor-pointer">
                                        <input
                                            type="checkbox"
                                            checked={batchOptions.headless}
                                            onChange={e =>
                                                setBatchOptions({ ...batchOptions, headless: e.target.checked })
                                            }
                                            className="rounded text-indigo-600"
                                        />
                                        <span>后台无头模式 (Headless 推荐服务器开启)</span>
                                    </label>
                                    <div className="flex items-center gap-2">
                                        <span className="opacity-70">账号间隔(ms):</span>
                                        <input
                                            type="number"
                                            value={batchOptions.accountDelay}
                                            onChange={e =>
                                                setBatchOptions({
                                                    ...batchOptions,
                                                    accountDelay: Number(e.target.value),
                                                })
                                            }
                                            className="w-20 rounded border px-2 py-1 bg-transparent text-xs"
                                        />
                                    </div>
                                </div>
                                <div>
                                    <label className="text-[11px] opacity-70 block mb-1">
                                        住宅代理 (服务器部署必填，防止 Google 机房 IP 风控):
                                    </label>
                                    <input
                                        type="text"
                                        value={batchOptions.proxy}
                                        onChange={e => setBatchOptions({ ...batchOptions, proxy: e.target.value })}
                                        placeholder="例如: http://username:password@proxy-host:port"
                                        className="w-full rounded border px-2.5 py-1.5 bg-transparent text-xs font-mono"
                                    />
                                </div>
                            </div>
                        </div>

                        {/* 底部按钮 */}
                        <div className="p-4 border-t border-slate-500/20 flex justify-end gap-3">
                            <button
                                onClick={() => setShowBatchModal(false)}
                                className="px-4 py-2 rounded-lg border text-xs font-medium hover:bg-slate-500/10 transition"
                            >
                                关闭
                            </button>
                            <button
                                onClick={startBatchAuth}
                                disabled={
                                    batchLoading ||
                                    selectedAccountIds.length === 0 ||
                                    batchTask?.status === 'running'
                                }
                                className="px-5 py-2 rounded-lg bg-indigo-600 hover:bg-indigo-700 text-white text-xs font-medium flex items-center gap-1.5 disabled:opacity-50 transition shadow-sm"
                            >
                                <Zap size={14} />
                                {batchLoading
                                    ? '正在启动...'
                                    : batchTask?.status === 'running'
                                    ? '任务执行中...'
                                    : `开始批量自动授权 (${selectedAccountIds.length} 个账号)`}
                            </button>
                        </div>
                    </div>
                </div>
            )}
        </div>
    );
};

export default GmailInboxView;
