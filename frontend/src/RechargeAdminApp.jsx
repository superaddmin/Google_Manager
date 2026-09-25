import React, { useEffect, useRef, useState } from 'react';
import { CreditCard, LogOut, Moon, RefreshCw, Sun, X } from 'lucide-react';
import api from './services/api';
import LoginPage from './components/LoginPage';

const STATUS_LABELS = {
    pending: '待处理', processing: '处理中', unknown: '待核对', completed: '已完成',
    failed: '失败', recalled: '已撤回', closed: '已关闭'
};
const MODE_LABELS = { disabled: '未启用', mock: '模拟测试', live: '真实履约' };

const ReconciliationForm = ({ detail, busy, fieldClass, onSubmit }) => {
    const mutation = detail.actions.reconcile_mutation ? detail.mutation : null;
    const [resolution, setResolution] = useState('created');
    const [source, setSource] = useState('provider_ticket');
    const [reference, setReference] = useState('');
    const [digest, setDigest] = useState('');
    const [observedAt, setObservedAt] = useState('');
    const [upstreamTaskNo, setUpstreamTaskNo] = useState(detail.upstream_task_no || '');
    const [upstreamStatus, setUpstreamStatus] = useState('processing');
    const [basis, setBasis] = useState('');
    const [confirmed, setConfirmed] = useState(false);

    const submit = event => {
        event.preventDefault();
        const payload = {
            confirmed, resolution: mutation ? 'not_applied' : resolution,
            evidence: { source, reference, sha256: digest, observed_at: new Date(observedAt).toISOString() }
        };
        if (mutation || resolution === 'created') {
            payload.upstream_task = {
                task_no: upstreamTaskNo, client_task_no: detail.task.task_no, status: upstreamStatus
            };
        }
        if (mutation) Object.assign(payload, { action: mutation.action, basis });
        onSubmit(payload, mutation?.operation_id);
    };

    return (
        <form onSubmit={submit} className="space-y-3 border-t border-slate-400/20 pt-4">
            <h3 className="font-semibold">人工对账</h3>
            <p className="text-sm opacity-70">请先取得并归档上游证据。超时或一次查询无结果不能作为结案依据。</p>
            {mutation ? <p className="text-sm">确认本次{mutation.action === 'recall' ? '撤回' : '关闭'}未执行</p> : (
                <label className="block text-sm">对账结论
                    <select className={fieldClass} value={resolution} onChange={event => setResolution(event.target.value)}>
                        <option value="created">上游已创建任务</option><option value="not_created">上游确认未创建任务</option>
                    </select>
                </label>
            )}
            <label className="block text-sm">证据来源
                <select className={fieldClass} value={source} onChange={event => setSource(event.target.value)}>
                    <option value="provider_ticket">上游工单</option><option value="provider_console">上游控制台</option><option value="upstream_api">上游 API</option>
                </select>
            </label>
            <label className="block text-sm">证据引用编号<input className={fieldClass} required minLength={8} maxLength={256} value={reference} onChange={event => setReference(event.target.value)} /></label>
            <label className="block text-sm">证据 SHA-256<input className={fieldClass} required pattern="[0-9a-fA-F]{64}" value={digest} onChange={event => setDigest(event.target.value)} /></label>
            <label className="block text-sm">证据观测时间<input className={fieldClass} type="datetime-local" required value={observedAt} onChange={event => setObservedAt(event.target.value)} /></label>
            {(mutation || resolution === 'created') && <>
                <label className="block text-sm">上游任务号<input className={fieldClass} required maxLength={128} value={upstreamTaskNo} onChange={event => setUpstreamTaskNo(event.target.value)} /></label>
                <label className="block text-sm">上游任务状态
                    <select className={fieldClass} value={upstreamStatus} onChange={event => setUpstreamStatus(event.target.value)}>
                        {Object.entries(STATUS_LABELS).filter(([status]) => status !== 'unknown').map(([status, label]) => <option key={status} value={status}>{label}</option>)}
                    </select>
                </label>
            </>}
            {mutation && <label className="block text-sm">未执行依据<textarea className={fieldClass} required minLength={8} maxLength={1024} value={basis} onChange={event => setBasis(event.target.value)} /></label>}
            <label className="flex gap-2 text-sm"><input type="checkbox" required checked={confirmed} onChange={event => setConfirmed(event.target.checked)} />已核对任务与证据，确认提交人工结论</label>
            <button disabled={busy || !confirmed} className="px-4 py-2 rounded-lg bg-amber-600 text-white disabled:opacity-50">提交人工对账</button>
        </form>
    );
};

const RechargeAdminApp = ({ embedded = false }) => {
    const [authenticated, setAuthenticated] = useState(false);
    const [darkMode, setDarkMode] = useState(() => {
        try { return JSON.parse(localStorage.getItem('darkMode')) === true; } catch { return false; }
    });
    const [overview, setOverview] = useState(null);
    const [result, setResult] = useState({ items: [], total: 0, page: 1, page_size: 20 });
    const [filters, setFilters] = useState({ q: '', status: '', source: '', page: 1, page_size: 20 });
    const [search, setSearch] = useState('');
    const [revision, setRevision] = useState(0);
    const [loading, setLoading] = useState(false);
    const [detail, setDetail] = useState(null);
    const [detailLoading, setDetailLoading] = useState(false);
    const [busy, setBusy] = useState(false);
    const [notice, setNotice] = useState(null);
    const selectedTask = useRef(null);
    const detailController = useRef(null);
    const actionPending = useRef(false);

    useEffect(() => {
        document.title = 'Chat GPT充值中心 · 管理后台';
        document.documentElement.classList.toggle('dark', darkMode);
        try { localStorage.setItem('darkMode', JSON.stringify(darkMode)); } catch {}
    }, [darkMode]);

    useEffect(() => () => detailController.current?.abort(), []);

    useEffect(() => {
        if (!authenticated) return;
        const controller = new AbortController();
        setLoading(true);
        Promise.all([
            api.getRechargeAdminOverview({ signal: controller.signal }),
            api.getRechargeAdminTasks(filters, { signal: controller.signal })
        ]).then(([summary, tasks]) => {
            if (controller.signal.aborted) return;
            setOverview(summary.data);
            setResult(tasks.data);
        }).catch(error => {
            if (!controller.signal.aborted) handleError(error);
        }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
        return () => controller.abort();
    }, [authenticated, filters, revision]);

    const handleError = error => {
        if (error.status === 401) {
            detailController.current?.abort();
            selectedTask.current = null;
            setDetailLoading(false);
            setAuthenticated(false);
            setDetail(null);
            setOverview(null);
            setResult({ items: [], total: 0, page: 1, page_size: 20 });
        }
        setNotice({ error: true, message: error.message || '操作失败，请稍后重试' });
    };

    const openTask = async taskNo => {
        detailController.current?.abort();
        const controller = new AbortController();
        detailController.current = controller;
        selectedTask.current = taskNo;
        setDetail(null);
        setDetailLoading(true);
        try {
            const response = await api.getRechargeAdminTask(taskNo, { signal: controller.signal });
            if (!controller.signal.aborted) setDetail(response.data);
        } catch (error) {
            if (!controller.signal.aborted) handleError(error);
        } finally {
            if (!controller.signal.aborted) setDetailLoading(false);
        }
    };

    const runAction = async (action, payload, operationId) => {
        if (!detail || actionPending.current) return;
        const taskNo = detail.task.task_no;
        if (['recall', 'close'].includes(action) && !window.confirm(`确认${action === 'recall' ? '撤回' : '关闭'}订单 ${taskNo}？`)) return;
        actionPending.current = true;
        setBusy(true);
        setNotice(null);
        try {
            if (action === 'reconcile') {
                await api.reconcileRechargeAdminTask(taskNo, payload, operationId);
            } else {
                await api.updateRechargeAdminTask(taskNo, action, { confirmed: true });
            }
            setNotice({ message: action === 'refresh' ? '订单状态已同步' : '订单操作已完成' });
        } catch (error) {
            handleError(error);
        } finally {
            if (selectedTask.current === taskNo) await openTask(taskNo);
            setRevision(current => current + 1);
            actionPending.current = false;
            setBusy(false);
        }
    };

    const logout = async () => {
        if (actionPending.current) return;
        try {
            await api.logout();
            detailController.current?.abort();
            selectedTask.current = null;
            setAuthenticated(false);
            setDetail(null);
            setOverview(null);
            setNotice(null);
            setResult({ items: [], total: 0, page: 1, page_size: 20 });
        } catch (error) { handleError(error); }
    };

    if (!authenticated) return <LoginPage title="Chat GPT充值中心" description="登录充值管理后台" compact={embedded} darkMode={darkMode} onLoginSuccess={() => setAuthenticated(true)} />;

    const panelClass = `rounded-xl border p-5 ${darkMode ? 'bg-slate-900 border-slate-800' : 'bg-white border-slate-200'}`;
    const fieldClass = `mt-1 w-full min-w-0 rounded-lg border px-3 py-2 ${darkMode ? 'bg-slate-800 border-slate-700' : 'bg-white border-slate-300'}`;
    const buttonClass = 'rounded-lg border border-slate-400/30 px-3 py-2 text-sm hover:bg-slate-400/10 disabled:opacity-40';
    const maxPage = Math.max(1, Math.ceil(result.total / result.page_size));

    return (
        <div className={`${embedded ? '' : 'min-h-screen'} ${darkMode ? 'bg-slate-950 text-slate-100' : 'bg-slate-50 text-slate-800'}`}>
            <header className="border-b border-slate-400/20">
                <div className="max-w-[1600px] mx-auto p-4 flex flex-wrap items-center justify-between gap-3">
                    <div className="flex items-center gap-3"><CreditCard className="text-emerald-500" /><div><h1 className="font-bold text-lg">Chat GPT充值中心</h1><p className="text-xs opacity-60">充值管理后台</p></div></div>
                    <nav className="flex items-center gap-2 text-sm">
                        {!embedded && <a className={buttonClass} href="/admin">CDK 卡密管理</a>}
                        <a className={buttonClass} href="/recharge">用户充值页</a><a className={buttonClass} href="/Googlemail">邮箱管理</a>
                        <button className={buttonClass} aria-label="切换主题" onClick={() => setDarkMode(current => !current)}>{darkMode ? <Sun size={18} /> : <Moon size={18} />}</button>
                        <button className={buttonClass} disabled={busy} onClick={logout} aria-label="退出登录"><LogOut size={18} /></button>
                    </nav>
                </div>
            </header>
            <main className="max-w-[1600px] mx-auto p-4 sm:p-6 space-y-5">
                {notice && <div role="alert" className={`rounded-lg px-4 py-3 ${notice.error ? 'bg-red-500/15 text-red-600' : 'bg-emerald-500/15 text-emerald-600'}`}>{notice.message}</div>}
                <section className="grid grid-cols-2 lg:grid-cols-4 gap-3" aria-label="充值统计">
                    {[
                        ['全部订单', overview?.total], ['处理中', overview ? (overview.counts.pending || 0) + (overview.counts.processing || 0) : null],
                        ['已完成', overview?.counts.completed || 0], ['待核对', overview?.pending_review]
                    ].map(([label, value]) => <div key={label} className={panelClass}><p className="text-sm opacity-60">{label}</p><p className="mt-2 text-2xl font-semibold">{overview ? value : '—'}</p></div>)}
                </section>
                <section className={panelClass} aria-label="运行配置">
                    <div className="flex flex-wrap items-center gap-3"><h2 className="font-semibold">运行配置</h2><span className="text-sm text-emerald-600">{overview ? MODE_LABELS[overview.mode] : '加载中'}</span></div>
                    <p className="mt-2 text-sm opacity-70">{overview?.mode === 'disabled' ? '充值履约当前未启用，可查询历史订单。' : '订单操作将交由当前充值服务处理。'}</p>
                </section>
                <div className={`grid gap-5 ${detail || detailLoading ? 'xl:grid-cols-[minmax(0,1fr)_420px]' : ''}`}>
                    <section className={`${panelClass} min-w-0`}>
                        <div className="flex justify-between items-center mb-4"><h2 className="font-semibold text-lg">充值订单</h2><button className={buttonClass} disabled={loading || busy} onClick={() => setRevision(current => current + 1)}><RefreshCw size={15} className="inline mr-1" />刷新列表</button></div>
                        <form className="flex flex-wrap items-end gap-3 mb-4" onSubmit={event => { event.preventDefault(); setFilters(current => ({ ...current, q: search.trim(), page: 1 })); }}>
                            <label className="flex-1 min-w-[180px] text-sm">搜索订单<input className={fieldClass} placeholder="订单号、完整邮箱或完整卡密" maxLength={256} value={search} onChange={event => setSearch(event.target.value)} /></label>
                            <label className="text-sm">订单状态<select className={fieldClass} value={filters.status} onChange={event => setFilters(current => ({ ...current, status: event.target.value, page: 1 }))}><option value="">全部状态</option>{Object.entries(STATUS_LABELS).map(([status, label]) => <option key={status} value={status}>{label}</option>)}</select></label>
                            <label className="text-sm">订单来源<select className={fieldClass} value={filters.source} onChange={event => setFilters(current => ({ ...current, source: event.target.value, page: 1 }))}><option value="">全部来源</option><option value="live">真实订单</option><option value="mock">模拟订单</option></select></label>
                            <button className={buttonClass} type="submit" disabled={loading}>查询</button>
                        </form>
                        <div className="overflow-x-auto">
                            <table className="w-full text-left text-sm"><thead><tr className="border-b border-slate-400/20">{['订单号 / 时间', '充值账号', '套餐', '状态', '操作'].map(label => <th className="py-3 pr-4 whitespace-nowrap" key={label}>{label}</th>)}</tr></thead>
                                <tbody>{result.items.map(task => <tr key={task.task_no} className="border-b border-slate-400/10">
                                    <td className="py-3 pr-4"><span className="font-mono whitespace-nowrap">{task.task_no}</span><p className="text-xs opacity-60 mt-1">{task.created_at}{task.is_mock ? ' · 模拟' : ''}</p></td>
                                    <td className="py-3 pr-4 break-all">{task.account_email}<p className="text-xs opacity-60">卡密尾号 {task.redeem_code_last4}</p></td>
                                    <td className="py-3 pr-4">{task.plan_type}</td><td className="py-3 pr-4 whitespace-nowrap">{STATUS_LABELS[task.status] || task.status_text}</td>
                                    <td><button className={buttonClass} disabled={busy} onClick={() => openTask(task.task_no)}>查看详情</button></td>
                                </tr>)}</tbody>
                            </table>
                        </div>
                        {!result.items.length && <p className="py-12 text-center opacity-60">{loading ? '正在加载订单...' : '暂无符合条件的充值订单'}</p>}
                        <div className="flex flex-wrap justify-between gap-3 items-center pt-4 text-sm"><span>共 {result.total} 条 · 第 {result.page} / {maxPage} 页</span><div className="flex gap-2"><button className={buttonClass} disabled={loading || filters.page <= 1} onClick={() => setFilters(current => ({ ...current, page: current.page - 1 }))}>上一页</button><button className={buttonClass} disabled={loading || filters.page >= maxPage} onClick={() => setFilters(current => ({ ...current, page: current.page + 1 }))}>下一页</button></div></div>
                    </section>
                    {(detail || detailLoading) && <aside className={`${panelClass} min-w-0 space-y-4 self-start`} aria-label="订单详情">
                        <div className="flex items-center justify-between"><h2 className="font-semibold text-lg">订单详情</h2><button className={buttonClass} disabled={busy} aria-label="关闭订单详情" onClick={() => { detailController.current?.abort(); selectedTask.current = null; setDetail(null); setDetailLoading(false); }}><X size={16} /></button></div>
                        {detailLoading ? <p>加载详情中...</p> : detail && <>
                            <dl className="space-y-2 text-sm">{[['订单号', detail.task.task_no], ['充值账号', detail.task.account_email], ['当前状态', STATUS_LABELS[detail.task.status] || detail.task.status_text], ['上游任务号', detail.upstream_task_no || '尚未绑定'], ['最近操作', detail.mutation ? `${detail.mutation.action} / ${detail.mutation.state}` : '暂无'], ['更新时间', detail.task.updated_at]].map(([label, value]) => <div className="flex gap-3" key={label}><dt className="w-24 shrink-0 opacity-60">{label}</dt><dd className="break-all">{value}</dd></div>)}</dl>
                            {detail.task.notice && <p className="text-sm text-amber-600">{detail.task.notice}</p>}
                            <div className="flex flex-wrap gap-2">{[['refresh', '同步上游状态'], ['recall', '撤回订单'], ['close', '关闭订单']].map(([action, label]) => <button key={action} className={buttonClass} disabled={busy || !detail.actions[action]} onClick={() => runAction(action)}>{label}</button>)}</div>
                            {(detail.actions.reconcile_task || detail.actions.reconcile_mutation) && <ReconciliationForm key={`${detail.task.task_no}:${detail.mutation?.operation_id || ''}`} detail={detail} busy={busy} fieldClass={fieldClass} onSubmit={(payload, operationId) => runAction('reconcile', payload, operationId)} />}
                            <div className="border-t border-slate-400/20 pt-4"><h3 className="font-semibold mb-2">人工对账记录</h3>{[detail.reconciliation, ...detail.mutation_reconciliations].filter(Boolean).map(audit => <div key={audit.operation_id || audit.id} className="text-sm mb-3 break-all"><p>{audit.created_at} · {audit.resolution}</p><p className="opacity-60">{audit.evidence_reference} · {audit.final_status}</p></div>)}{!detail.reconciliation && !detail.mutation_reconciliations.length && <p className="text-sm opacity-60">暂无人工对账记录</p>}</div>
                        </>}
                    </aside>}
                </div>
            </main>
        </div>
    );
};

export default RechargeAdminApp;
