import React, { useEffect, useMemo, useRef, useState } from 'react';
import {
    AlertTriangle,
    CheckCircle2,
    History,
    Loader2,
    MailCheck,
    Play,
    RefreshCw,
    Search,
    Square,
} from 'lucide-react';
import api from '../services/api';

const terminalStatuses = new Set(['completed', 'failed', 'cancelled']);

const statusLabels = {
    pending: '等待启动',
    running: '执行中',
    finalizing: '正在同步结果',
    completed: '已完成',
    failed: '执行失败',
    cancelled: '已取消',
};

const numericOptionRules = [
    { key: 'slowMo', label: '操作延迟', min: 0, max: 5000 },
    { key: 'accountDelay', label: '账号间隔', min: 0, max: 600000 },
    { key: 'accountsPerRecovery', label: '恢复邮箱复用数', min: 1, max: 100 },
    { key: 'maxRuntimeMinutes', label: '最长运行', min: 1, max: 480 },
];

const GooglemailView = ({ accounts, darkMode, onTaskComplete }) => {
    const [selectedIds, setSelectedIds] = useState([]);
    const [search, setSearch] = useState('');
    const [capability, setCapability] = useState(null);
    const [task, setTask] = useState(null);
    const [taskHistory, setTaskHistory] = useState([]);
    const [loading, setLoading] = useState(true);
    const [submitting, setSubmitting] = useState(false);
    const [error, setError] = useState('');
    const pollingRef = useRef(false);
    const taskRequestTokenRef = useRef(0);
    const [options, setOptions] = useState({
        headless: true,
        slowMo: 200,
        accountDelay: 5000,
        accountsPerRecovery: 5,
        maxRuntimeMinutes: 120,
        recoveryEmails: '',
    });

    const filteredAccounts = useMemo(() => {
        const keyword = search.trim().toLowerCase();
        if (!keyword) return accounts;
        return accounts.filter(account => (
            account.email.toLowerCase().includes(keyword)
            || (account.remark || '').toLowerCase().includes(keyword)
        ));
    }, [accounts, search]);

    const isRunning = task && !terminalStatuses.has(task.status);
    const processedCount = task ? task.completedCount + task.failedCount : 0;
    const progress = task?.totalCount
        ? Math.round((processedCount / task.totalCount) * 100)
        : 0;

    const loadStatus = async () => {
        try {
            setError('');
            const result = await api.getGooglemailStatus();
            if (result.success) {
                setCapability(result.data);
                const latestTask = result.data.activeTask || result.data.latestTask;
                if (latestTask) setTask(latestTask);
            } else {
                setError(result.message || '加载 Googlemail 状态失败');
            }
        } catch (requestError) {
            console.error('加载 Googlemail 状态失败:', requestError);
            setError(requestError.message || '加载 Googlemail 状态失败');
        } finally {
            setLoading(false);
        }
    };

    const refreshTask = async (taskId) => {
        if (pollingRef.current) return;
        pollingRef.current = true;
        const requestToken = ++taskRequestTokenRef.current;
        try {
            const result = await api.getGooglemailTask(taskId);
            if (requestToken === taskRequestTokenRef.current && result.success) {
                setTask(result.data);
            }
        } catch (requestError) {
            console.error('刷新 Googlemail 任务失败:', requestError);
        } finally {
            pollingRef.current = false;
        }
    };

    const loadHistory = async () => {
        try {
            const result = await api.getGooglemailTasks(20);
            if (result.success) {
                setTaskHistory(result.data || []);
            }
        } catch (requestError) {
            console.error('加载 Googlemail 任务历史失败:', requestError);
        }
    };

    useEffect(() => {
        loadStatus();
        loadHistory();
    }, []);

    useEffect(() => {
        if (!isRunning) return undefined;
        const timer = setInterval(() => refreshTask(task.taskId), 1500);
        return () => {
            clearInterval(timer);
            taskRequestTokenRef.current += 1;
        };
    }, [isRunning, task?.taskId]);

    useEffect(() => {
        if (task && terminalStatuses.has(task.status)) {
            onTaskComplete();
            loadHistory();
        }
    }, [task?.taskId, task?.status]);

    const toggleAccount = (accountId) => {
        setSelectedIds(current => current.includes(accountId)
            ? current.filter(id => id !== accountId)
            : [...current, accountId]);
    };

    const toggleAllVisible = () => {
        const visibleIds = filteredAccounts.map(account => account.id);
        const allSelected = visibleIds.length > 0
            && visibleIds.every(accountId => selectedIds.includes(accountId));
        setSelectedIds(current => allSelected
            ? current.filter(accountId => !visibleIds.includes(accountId))
            : [...new Set([...current, ...visibleIds])]);
    };

    const startTask = async () => {
        if (!selectedIds.length || isRunning) return;
        const invalidOption = numericOptionRules.find(({ key, min, max }) => (
            !Number.isInteger(options[key]) || options[key] < min || options[key] > max
        ));
        if (invalidOption) {
            setError(`${invalidOption.label}必须在 ${invalidOption.min} 到 ${invalidOption.max} 之间`);
            return;
        }
        const confirmed = window.confirm(`确认启动 Googlemail 任务？\n\n本次将处理 ${selectedIds.length} 个账号。`);
        if (!confirmed) return;

        setSubmitting(true);
        setError('');
        try {
            const result = await api.startGooglemailTask(selectedIds, {
                ...options,
                recoveryEmails: options.recoveryEmails
                    .split(/[,\r\n]+/)
                    .map(value => value.trim())
                    .filter(Boolean),
            });
            if (result.success) {
                setTask(result.data);
            } else {
                setError(result.message || '启动 Googlemail 任务失败');
            }
        } catch (requestError) {
            console.error('启动 Googlemail 任务失败:', requestError);
            setError(requestError.message || '启动 Googlemail 任务失败');
        } finally {
            setSubmitting(false);
        }
    };

    const cancelTask = async () => {
        if (!task || !isRunning) return;
        const confirmed = window.confirm('确认取消当前 Googlemail 任务？');
        if (!confirmed) return;
        try {
            const result = await api.cancelGooglemailTask(task.taskId);
            if (result.success) setTask(result.data);
            else setError(result.message || '取消任务失败');
        } catch (requestError) {
            console.error('取消 Googlemail 任务失败:', requestError);
            setError(requestError.message || '取消任务失败');
        }
    };

    const panelClass = darkMode
        ? 'bg-slate-800 border-slate-700'
        : 'bg-white border-slate-200';
    const inputClass = darkMode
        ? 'bg-slate-900 border-slate-600 text-slate-100 placeholder-slate-500'
        : 'bg-white border-slate-200 text-slate-800 placeholder-slate-400';

    if (loading) {
        return (
            <div className="min-h-[420px] flex items-center justify-center">
                <Loader2 className="animate-spin text-blue-500" size={34} />
            </div>
        );
    }

    const executionReady = capability?.available && capability?.executionEnabled;
    const allVisibleSelected = filteredAccounts.length > 0
        && filteredAccounts.every(account => selectedIds.includes(account.id));

    return (
        <div className="space-y-5">
            <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
                <div className="flex items-center gap-3">
                    <div className="p-2.5 bg-cyan-600 text-white rounded-lg">
                        <MailCheck size={22} />
                    </div>
                    <div>
                        <h1 className="text-xl font-bold">Googlemail</h1>
                        <p className={`text-sm ${darkMode ? 'text-slate-400' : 'text-slate-500'}`}>
                            2FA 与恢复邮箱任务
                        </p>
                    </div>
                </div>
                <button
                    onClick={loadStatus}
                    className={`p-2.5 rounded-lg border transition-colors ${panelClass}`}
                    title="刷新运行状态"
                >
                    <RefreshCw size={18} />
                </button>
            </div>

            {!executionReady && (
                <div className={`flex items-center gap-3 px-4 py-3 border rounded-lg ${darkMode
                    ? 'bg-amber-950/30 border-amber-800 text-amber-300'
                    : 'bg-amber-50 border-amber-200 text-amber-700'}`}>
                    <AlertTriangle size={18} className="flex-shrink-0" />
                    <span className="text-sm">
                        {capability?.available ? '当前环境已关闭实际执行' : 'Googlemail 运行依赖未就绪'}
                    </span>
                </div>
            )}

            {error && (
                <div className="flex items-center gap-3 px-4 py-3 bg-red-50 border border-red-200 text-red-700 rounded-lg">
                    <AlertTriangle size={18} />
                    <span className="text-sm">{error}</span>
                </div>
            )}

            {task && (
                <section className={`border rounded-lg overflow-hidden ${panelClass}`}>
                    <div className="px-5 py-4 flex flex-col sm:flex-row sm:items-center justify-between gap-3 border-b border-inherit">
                        <div className="flex items-center gap-3">
                            {task.status === 'completed'
                                ? <CheckCircle2 className="text-green-500" size={20} />
                                : task.status === 'failed'
                                    ? <AlertTriangle className="text-red-500" size={20} />
                                    : <Loader2 className={isRunning ? 'animate-spin text-blue-500' : 'text-slate-400'} size={20} />}
                            <div>
                                <p className="font-bold">{statusLabels[task.status] || task.status}</p>
                                <p className={`text-xs font-mono ${darkMode ? 'text-slate-500' : 'text-slate-400'}`}>
                                    {task.taskId.slice(0, 12)}
                                </p>
                                {task.errorCode && (
                                    <p className="text-xs font-mono text-red-500 mt-1">{task.errorCode}</p>
                                )}
                            </div>
                        </div>
                        {isRunning && (
                            <button
                                onClick={cancelTask}
                                className="inline-flex items-center justify-center gap-2 px-3 py-2 bg-red-600 text-white rounded-lg text-sm font-bold hover:bg-red-700"
                            >
                                <Square size={14} />
                                取消任务
                            </button>
                        )}
                    </div>
                    <div className="p-5">
                        <div className={`h-2 rounded-full overflow-hidden ${darkMode ? 'bg-slate-700' : 'bg-slate-100'}`}>
                            <div className="h-full bg-cyan-500 transition-all" style={{ width: `${progress}%` }} />
                        </div>
                        <div className="grid grid-cols-2 sm:grid-cols-5 gap-3 mt-4 text-center">
                            {[
                                ['总数', task.totalCount],
                                ['完成', task.completedCount],
                                ['失败', task.failedCount],
                                ['已同步', task.syncedCount],
                                ['待复核', task.manualReviewCount],
                            ].map(([label, value]) => (
                                <div key={label} className={`py-2 rounded-md ${darkMode ? 'bg-slate-900' : 'bg-slate-50'}`}>
                                    <p className="text-lg font-bold">{value}</p>
                                    <p className={`text-xs ${darkMode ? 'text-slate-500' : 'text-slate-400'}`}>{label}</p>
                                </div>
                            ))}
                        </div>
                    </div>
                </section>
            )}

            {taskHistory.length > 0 && (
                <section className={`border rounded-lg overflow-hidden ${panelClass}`}>
                    <div className="px-5 py-3 flex items-center gap-2 border-b border-inherit">
                        <History size={16} className={darkMode ? 'text-slate-400' : 'text-slate-500'} />
                        <h2 className="font-bold">历史任务</h2>
                        <span className={`text-xs ${darkMode ? 'text-slate-500' : 'text-slate-400'}`}>
                            （最近 {taskHistory.length} 条）
                        </span>
                    </div>
                    <div className="overflow-x-auto">
                        <table className="w-full text-sm">
                            <thead className={darkMode ? 'bg-slate-900 text-slate-400' : 'bg-slate-50 text-slate-500'}>
                                <tr>
                                    <th className="px-4 py-2.5 text-left">开始时间</th>
                                    <th className="px-3 py-2.5 text-left">状态</th>
                                    <th className="px-3 py-2.5 text-center">总数</th>
                                    <th className="px-3 py-2.5 text-center">完成</th>
                                    <th className="px-3 py-2.5 text-center">失败</th>
                                    <th className="px-3 py-2.5 text-center">已同步</th>
                                    <th className="px-3 py-2.5 text-center">待复核</th>
                                    <th className="px-4 py-2.5 text-left">错误码</th>
                                </tr>
                            </thead>
                            <tbody className="divide-y divide-inherit">
                                {taskHistory.map(item => (
                                    <tr key={item.taskId} className={darkMode ? 'hover:bg-slate-700/50' : 'hover:bg-slate-50'}>
                                        <td className="px-4 py-2.5 font-mono text-xs whitespace-nowrap">
                                            {item.startedAt || item.createdAt}
                                        </td>
                                        <td className="px-3 py-2.5">
                                            <span className={`inline-flex items-center gap-1.5 text-xs font-bold ${
                                                item.status === 'completed' ? 'text-green-500'
                                                    : item.status === 'failed' ? 'text-red-500'
                                                        : 'text-slate-400'}`}>
                                                {item.status === 'completed' && <CheckCircle2 size={13} />}
                                                {item.status === 'failed' && <AlertTriangle size={13} />}
                                                {statusLabels[item.status] || item.status}
                                            </span>
                                        </td>
                                        <td className="px-3 py-2.5 text-center">{item.totalCount}</td>
                                        <td className="px-3 py-2.5 text-center text-green-500 font-bold">{item.completedCount}</td>
                                        <td className="px-3 py-2.5 text-center text-red-500 font-bold">{item.failedCount}</td>
                                        <td className="px-3 py-2.5 text-center">{item.syncedCount}</td>
                                        <td className="px-3 py-2.5 text-center">{item.manualReviewCount}</td>
                                        <td className="px-4 py-2.5 font-mono text-xs text-red-500">{item.errorCode || '-'}</td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                </section>
            )}

            <div className="grid grid-cols-1 xl:grid-cols-[minmax(0,1fr)_340px] gap-5">
                <section className={`border rounded-lg overflow-hidden ${panelClass}`}>
                    <div className="p-4 border-b border-inherit flex flex-col sm:flex-row sm:items-center gap-3">
                        <label className="flex items-center gap-3 text-sm font-bold">
                            <input
                                type="checkbox"
                                checked={allVisibleSelected}
                                onChange={toggleAllVisible}
                                className="w-4 h-4 accent-blue-600"
                            />
                            账号 ({selectedIds.length}/{accounts.length})
                        </label>
                        <div className="relative sm:ml-auto w-full sm:w-72">
                            <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
                            <input
                                value={search}
                                onChange={event => setSearch(event.target.value)}
                                placeholder="搜索邮箱或备注"
                                className={`w-full pl-9 pr-3 py-2 border rounded-lg text-sm outline-none focus:ring-2 focus:ring-blue-500 ${inputClass}`}
                            />
                        </div>
                    </div>
                    <div className="max-h-[480px] overflow-auto">
                        <table className="w-full text-sm">
                            <thead className={darkMode ? 'bg-slate-900 text-slate-400' : 'bg-slate-50 text-slate-500'}>
                                <tr>
                                    <th className="w-12 px-4 py-3" />
                                    <th className="px-3 py-3 text-left">邮箱</th>
                                    <th className="px-3 py-3 text-left">备注</th>
                                    <th className="px-4 py-3 text-center">2FA</th>
                                </tr>
                            </thead>
                            <tbody className="divide-y divide-inherit">
                                {filteredAccounts.map(account => (
                                    <tr key={account.id} className={darkMode ? 'hover:bg-slate-700/50' : 'hover:bg-slate-50'}>
                                        <td className="px-4 py-3 text-center">
                                            <input
                                                type="checkbox"
                                                checked={selectedIds.includes(account.id)}
                                                onChange={() => toggleAccount(account.id)}
                                                className="w-4 h-4 accent-blue-600"
                                                aria-label={`选择 ${account.email}`}
                                            />
                                        </td>
                                        <td className="px-3 py-3 font-mono break-all">{account.email}</td>
                                        <td className={`px-3 py-3 ${darkMode ? 'text-slate-400' : 'text-slate-500'}`}>
                                            {account.remark || '-'}
                                        </td>
                                        <td className="px-4 py-3 text-center">
                                            <span className={`text-xs font-bold ${account.secret ? 'text-green-500' : 'text-amber-500'}`}>
                                                {account.secret ? '已配置' : '未配置'}
                                            </span>
                                        </td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                        {filteredAccounts.length === 0 && (
                            <div className={`py-16 text-center ${darkMode ? 'text-slate-500' : 'text-slate-400'}`}>暂无账号</div>
                        )}
                    </div>
                </section>

                <section className={`border rounded-lg p-5 h-fit ${panelClass}`}>
                    <h2 className="font-bold mb-4">运行参数</h2>
                    <div className="space-y-4">
                        <label className="flex items-center justify-between gap-3 text-sm">
                            <span>无头模式</span>
                            <input
                                type="checkbox"
                                checked={options.headless}
                                onChange={event => setOptions(current => ({ ...current, headless: event.target.checked }))}
                                className="w-4 h-4 accent-blue-600"
                            />
                        </label>
                        {[
                            ['操作延迟 (ms)', 'slowMo', 0, 5000],
                            ['账号间隔 (ms)', 'accountDelay', 0, 600000],
                            ['每个恢复邮箱账号数', 'accountsPerRecovery', 1, 100],
                            ['最长运行 (分钟)', 'maxRuntimeMinutes', 1, 480],
                        ].map(([label, key, min, max]) => (
                            <label key={key} className="block text-sm">
                                <span className={`block mb-1.5 ${darkMode ? 'text-slate-300' : 'text-slate-600'}`}>{label}</span>
                                <input
                                type="number"
                                min={min}
                                max={max}
                                step="1"
                                value={Number.isFinite(options[key]) ? options[key] : ''}
                                onChange={event => {
                                    const value = event.target.value;
                                    const parsed = value === '' ? '' : Number(value);
                                    setOptions(current => ({
                                        ...current,
                                        [key]: Number.isFinite(parsed) ? parsed : '',
                                    }));
                                }}
                                    className={`w-full px-3 py-2 border rounded-lg outline-none focus:ring-2 focus:ring-blue-500 ${inputClass}`}
                                />
                            </label>
                        ))}
                        <label className="block text-sm">
                            <span className={`block mb-1.5 ${darkMode ? 'text-slate-300' : 'text-slate-600'}`}>恢复邮箱池</span>
                            <textarea
                                rows="3"
                                value={options.recoveryEmails}
                                onChange={event => setOptions(current => ({ ...current, recoveryEmails: event.target.value }))}
                                placeholder="每行一个邮箱"
                                className={`w-full px-3 py-2 border rounded-lg outline-none resize-y focus:ring-2 focus:ring-blue-500 ${inputClass}`}
                            />
                        </label>
                        <button
                            onClick={startTask}
                            disabled={!executionReady || !selectedIds.length || isRunning || submitting}
                            className="w-full inline-flex items-center justify-center gap-2 py-3 bg-cyan-600 text-white rounded-lg font-bold hover:bg-cyan-700 disabled:bg-slate-400 disabled:cursor-not-allowed"
                        >
                            {submitting ? <Loader2 size={18} className="animate-spin" /> : <Play size={18} />}
                            启动任务
                        </button>
                    </div>
                </section>
            </div>
        </div>
    );
};

export default GooglemailView;
