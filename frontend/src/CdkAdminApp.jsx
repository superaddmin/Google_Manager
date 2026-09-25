import React, { useEffect, useRef, useState } from 'react';
import { cdkRequest, downloadCdkExport, requestKey, setCdkCsrf } from './services/cdk';

const fieldClass = 'w-full border border-slate-300 rounded-lg p-2 bg-white text-slate-900';
const buttonClass = 'border border-slate-300 rounded-lg px-3 py-2 text-sm hover:bg-slate-100 disabled:opacity-40';
const primaryClass = buttonClass + ' bg-emerald-700 text-white hover:bg-emerald-800';
const stateLabels = { draft: '草稿', active: '已激活', frozen: '冻结', void: '作废', closed: '关闭', unused: '未使用',
    reserved: '已占用', redeemed: '已核销', unassigned: '未分发', distributed: '已分发', claimed: '已领取', quarantine: '待验证',
    available: '可分配', allocated: '已分配', consumed: '已消费', unusable: '不可用', pending: '待审批', applied: '已执行',
    rejected: '已拒绝', completed: '已完成', processing: '处理中', unknown: '待核对', succeeded: '成功', released: '已释放',
    prepared: '已准备', dispatching: '提交中', cancelled: '已取消' };
const actionLabels = { activate: '激活批次', unfreeze_batch: '解冻批次', close_batch: '关闭批次', unfreeze_card: '解冻卡密',
    void: '作废卡密', extend: '延期', reissue: '补发', export: '导出', release: '凭证据释放', resolve_mutation: '核对未执行操作' };
const formatTime = seconds => seconds ? new Date(seconds * 1000).toLocaleString() : '—';

function FormDialog({ form, busy, onClose, onSubmit }) {
    const [values, setValues] = useState(() => Object.fromEntries(form.fields.map(field => [field.name, field.value ?? (field.type === 'checkbox' ? false : '')])));
    const submit = event => {
        event.preventDefault();
        const payload = { ...values };
        for (const field of form.fields) {
            if (field.type === 'number') payload[field.name] = Number(payload[field.name]);
            if (field.type === 'datetime-local') payload[field.name] = new Date(payload[field.name]).toISOString();
        }
        onSubmit(payload);
    };
    return <div className="fixed inset-0 bg-slate-900/40 z-50 flex justify-center items-center p-4"><form role="dialog" aria-label={form.title} onSubmit={submit} className="bg-white rounded-xl p-6 w-full max-w-xl max-h-[90vh] overflow-auto space-y-4">
        <h2 className="text-lg font-bold">{form.title}</h2>{form.description && <p className="text-sm text-slate-600">{form.description}</p>}
        {form.fields.map(field => <label className="block text-sm space-y-1" key={field.name}><span>{field.label}</span>
            {field.options ? <select aria-label={field.label} className={fieldClass} required value={values[field.name]} onChange={event => setValues({ ...values, [field.name]: event.target.value })}>
                <option value="">请选择</option>{field.options.map(option => <option key={option.value} value={option.value}>{option.label}</option>)}
            </select> : field.type === 'textarea' ? <textarea aria-label={field.label} className={fieldClass} rows={5} required={field.required !== false} maxLength={field.maxLength || 131072} value={values[field.name]} onChange={event => setValues({ ...values, [field.name]: event.target.value })} /> :
                <input aria-label={field.label} className={field.type === 'checkbox' ? 'ml-3' : fieldClass} type={field.type || 'text'} autoComplete={field.type === 'password' ? 'new-password' : 'off'} min={field.min} max={field.max} maxLength={field.maxLength || 256} required={field.type !== 'checkbox' && field.required !== false}
                    {...(field.type === 'checkbox' ? { checked: values[field.name] } : { value: values[field.name] })}
                    onChange={event => setValues({ ...values, [field.name]: field.type === 'checkbox' ? event.target.checked : event.target.value })} />}
        </label>)}
        <div className="flex gap-3"><button className={primaryClass} disabled={busy}>确认提交</button><button className={buttonClass} type="button" disabled={busy} onClick={onClose}>取消</button></div>
    </form></div>;
}

export default function CdkAdminApp({ embedded = false }) {
    const [configuration, setConfiguration] = useState(null);
    const [staff, setStaff] = useState(null);
    const [username, setUsername] = useState('');
    const [password, setPassword] = useState('');
    const [tab, setTab] = useState('batches');
    const [items, setItems] = useState([]);
    const [cursor, setCursor] = useState(null);
    const [channels, setChannels] = useState([]);
    const [benefits, setBenefits] = useState([]);
    const [summary, setSummary] = useState(null);
    const [batchFilter, setBatchFilter] = useState('');
    const [searchCode, setSearchCode] = useState('');
    const [selection, setSelection] = useState([]);
    const [form, setForm] = useState(null);
    const [preview, setPreview] = useState(null);
    const [secretTicket, setSecretTicket] = useState(null);
    const [notice, setNotice] = useState('');
    const [busy, setBusy] = useState(false);
    const pending = useRef(false);
    const queryVersion = useRef(0);
    const ticketTimer = useRef(null);
    const can = capability => staff?.capabilities.includes(capability) && (!['stock', 'catalog', 'iam'].includes(capability) || staff.scopes.includes('*'));

    useEffect(() => {
        document.title = embedded ? 'Chat GPT充值中心 · 管理后台' : 'Chat GPT充值中心 · 卡密工作台';
        const controller = new AbortController();
        cdkRequest('/config', undefined, { signal: controller.signal }).then(setConfiguration).catch(error => { if (!controller.signal.aborted) setNotice(error.message); });
        cdkRequest('/admin/session', undefined, { signal: controller.signal }).then(result => { setCdkCsrf(result.csrf_token); setStaff(result); }).catch(() => {});
        return () => { controller.abort(); clearTimeout(ticketTimer.current); setCdkCsrf(''); };
    }, [embedded]);

    const load = async (nextCursor = '', append = false) => {
        const revision = ++queryVersion.current;
        const parameters = new URLSearchParams({ limit: '50' });
        if (nextCursor) parameters.set('cursor', nextCursor);
        if (tab === 'cards' && batchFilter) parameters.set('batch_id', batchFilter);
        const [result, channelData, benefitData, report] = await Promise.all([
            cdkRequest('/admin/' + tab + '?' + parameters), cdkRequest('/admin/channels'), cdkRequest('/admin/benefits'), cdkRequest('/admin/reports')
        ]);
        if (queryVersion.current !== revision) return;
        setItems(previous => append ? [...previous, ...result.items] : result.items);
        setCursor(result.next_cursor);
        setChannels(channelData.items); setBenefits(benefitData.items); setSummary(report);
    };

    const perform = async action => {
        if (pending.current) return;
        pending.current = true; setBusy(true); setNotice('');
        try { await action(); }
        catch (error) {
            setNotice(error.message);
            if (error.status === 401) { setStaff(null); setCdkCsrf(''); setItems([]); setSecretTicket(null); }
        } finally { pending.current = false; setBusy(false); }
    };

    useEffect(() => {
        if (!staff) return;
        setSelection([]); setItems([]); setCursor(null);
        load().catch(error => setNotice(error.message));
        return () => { queryVersion.current += 1; };
    }, [staff, tab, batchFilter]);

    const openForm = (title, fields, submit, description = '') => setForm({ id: requestKey(), title, fields, submit, description });
    const reasonField = { name: 'reason', label: '操作原因', maxLength: 256 };
    const options = rows => rows.map(row => ({ value: row.id, label: row.name || row.code }));
    const benefitField = { name: 'benefit_id', label: '权益版本', options: options(benefits) };
    const requestApproval = (action, row) => {
        const extra = action === 'extend' ? [{ name: 'expires_at', label: '延长至（本地时间）', type: 'datetime-local' }] : ['release', 'resolve_mutation'].includes(action) ? [
            { name: 'reference', label: '归档的供应商证据引用' }, { name: 'evidence_sha256', label: '证据 SHA-256', maxLength: 64 },
            { name: 'observed_at', label: '证据观测时间', type: 'datetime-local' }
        ] : [];
        openForm('申请' + actionLabels[action], [reasonField, ...extra], async (values, key) => {
            await cdkRequest('/admin/approvals', { ...values, action, target_id: row.id, version: row.version,
                ...(action === 'release' ? { decision: 'not_consumed' } : {}),
                ...(action === 'resolve_mutation' ? { decision: 'not_applied', mutation_id: row.mutation_id } : {}) }, { key });
            setNotice('审批申请已创建，请另一位具名复核人员处理。');
        }, action === 'release' ? '只有供应商明确确认未消费时才可申请。超时、查不到记录均不能作为释放依据。' : '此操作需要独立复核，审批绑定当前对象版本。');
    };

    const freeze = (kind, row) => openForm('冻结' + (kind === 'batches' ? '批次' : '卡密'), [reasonField],
        (values, key) => cdkRequest('/admin/' + kind + '/' + row.id + '/freeze', { ...values, version: row.version }, { key }));

    const toolbar = () => <div className="flex flex-wrap gap-3">
        {tab === 'channels' && can('catalog') && <button className={primaryClass} onClick={() => openForm('新建渠道', [{ name: 'code', label: '渠道编号' }, { name: 'name', label: '渠道名称' }], (values, key) => cdkRequest('/admin/channels', values, { key }))}>新建渠道</button>}
        {tab === 'benefits' && can('catalog') && <button className={primaryClass} onClick={() => openForm('新增权益版本', [
            { name: 'product_code', label: '产品编号' }, { name: 'revision', label: '修订号', type: 'number', min: 1, value: 1 }, { name: 'name', label: '权益名称' },
            { name: 'plan_type', label: '套餐', options: ['PLUS', 'PRO'].map(value => ({ value, label: value })) },
            { name: 'reference_amount_minor', label: '参考面额（分，不计收入）', type: 'number', min: 0, value: 0 }, { name: 'currency', label: '币种', value: 'CNY', maxLength: 3 },
            { name: 'renewal_allowed', label: '允许续费', type: 'checkbox' }
        ], (values, key) => cdkRequest('/admin/benefits', values, { key }))}>新增权益版本</button>}
        {tab === 'stocks' && can('stock') && <button className={primaryClass} onClick={() => openForm('导入供应商库存', [benefitField,
            { name: 'content', label: '供应商原码（每行一份，最多 100 行）', type: 'textarea' }, { name: 'purchase_ref', label: '采购凭据引用' },
            { name: 'valid_until', label: '供应商有效期（本地时间）', type: 'datetime-local' }
        ], async (values, key) => setPreview(await cdkRequest('/admin/imports/preview', values, { key })), '导入后进入隔离库存，需要逐份验证。此处不能导入 GM1 平台兑换码。')}>导入库存</button>}
        {tab === 'batches' && can('issue') && <button className={primaryClass} onClick={() => openForm('新建发行批次', [
            { name: 'name', label: '批次名称' }, benefitField, { name: 'channel_id', label: '渠道', options: options(channels) },
            { name: 'quantity', label: '发行数量', type: 'number', min: 1, max: 100, value: 1 },
            { name: 'not_before', label: '生效时间（本地时间）', type: 'datetime-local' }, { name: 'expires_at', label: '到期时间（本地时间）', type: 'datetime-local' },
            { name: 'customer_required', label: '必须验证客户邮箱', type: 'checkbox' }
        ], (values, key) => cdkRequest('/admin/batches', values, { key }))}>新建批次</button>}
        {tab === 'cards' && <><input aria-label="批次编号筛选" placeholder="按批次编号筛选" className="border rounded-lg p-2" value={batchFilter} onChange={event => setBatchFilter(event.target.value)} />
            <input aria-label="完整卡密搜索" placeholder="输入完整平台码进行精确搜索" className="border rounded-lg p-2" autoComplete="off" value={searchCode} onChange={event => setSearchCode(event.target.value)} />
            <button className={buttonClass} onClick={() => perform(async () => { setItems([await cdkRequest('/admin/cards/search', { code: searchCode })]); setCursor(null); setSearchCode(''); })}>精确搜索</button>
            {can('export') && <button disabled={!selection.length} className={buttonClass} onClick={() => openForm('申请导出平台卡密', [reasonField], async (values, key) => {
                const selected = items.filter(item => selection.includes(item.id));
                if (new Set(selected.map(item => item.batch_id)).size !== 1) throw new Error('请选择同一批次中的卡密');
                const batches = await cdkRequest('/admin/batches?limit=100');
                const batch = batches.items.find(item => item.id === selected[0].batch_id);
                if (!batch) throw new Error('请刷新批次列表后重试');
                await cdkRequest('/admin/approvals', { ...values, action: 'export', target_id: batch.id, version: batch.version, card_ids: selection }, { key });
            }, '先分发再导出；指定客户的卡密通过领取票据交付。导出需另一名员工审批，下载有效期 15 分钟。')}>申请导出所选 {selection.length} 张</button>}</>}
        {tab === 'users' && can('iam') && <button className={primaryClass} onClick={() => openForm('新增充值员工', [
            { name: 'username', label: '用户名' }, { name: 'password', label: '密码（至少 12 字符）', type: 'password' },
            { name: 'role', label: '角色', options: ['admin', 'operator', 'reviewer', 'support', 'auditor'].map(value => ({ value, label: value })) },
            { name: 'scope', label: '渠道范围', options: [{ value: '*', label: '全局范围' }, ...options(channels)] }
        ], ({ scope, ...values }, key) => cdkRequest('/admin/users', { ...values, scopes: [scope] }, { key }))}>新增员工</button>}
        <button disabled={busy} className={buttonClass} onClick={() => perform(load)}>刷新</button>
    </div>;

    const controls = row => <div className="flex flex-wrap gap-2">
        {tab === 'batches' && <>
            <button className={buttonClass} onClick={() => { setBatchFilter(row.id); setTab('cards'); }}>查看卡密</button>
            {can('issue') && row.state === 'draft' && <><button className={buttonClass} onClick={() => perform(async () => { await cdkRequest('/admin/batches/' + row.id + '/generate', { version: row.version }); await load(); })}>生成 CDK 卡密</button><button className={buttonClass} onClick={() => requestApproval('activate', row)}>申请激活</button></>}
            {can('control') && row.state === 'draft' && <button className={buttonClass} onClick={() => requestApproval('close_batch', row)}>申请取消草稿批次</button>}
            {can('control') && row.state === 'active' && <button className={buttonClass} onClick={() => freeze('batches', row)}>冻结</button>}
            {can('control') && row.state === 'frozen' && <><button className={buttonClass} onClick={() => requestApproval('unfreeze_batch', row)}>申请解冻</button><button className={buttonClass} onClick={() => requestApproval('close_batch', row)}>申请关闭</button></>}
        </>}
        {tab === 'stocks' && can('stock') && ['quarantine', 'available'].includes(row.state) && <button className={buttonClass} onClick={() => perform(async () => { await cdkRequest('/admin/stocks/' + row.id + '/verify', { version: row.version }); await load(); })}>验证供应商库存</button>}
        {tab === 'cards' && <>
            {can('control') && row.control === 'active' && <button className={buttonClass} onClick={() => freeze('cards', row)}>冻结</button>}
            {can('control') && row.usage === 'unused' && row.control !== 'void' && <>
                {row.control === 'frozen' && <><button className={buttonClass} onClick={() => requestApproval('unfreeze_card', row)}>申请解冻</button><button className={buttonClass} onClick={() => requestApproval('reissue', row)}>申请补发</button></>}
                <button className={buttonClass} onClick={() => requestApproval('void', row)}>申请作废</button><button className={buttonClass} onClick={() => requestApproval('extend', row)}>申请延期</button>
            </>}
            {can('distribute') && row.distribution === 'unassigned' && row.control === 'active' && <button className={buttonClass} onClick={() => openForm('分发卡密', [
                { name: 'recipient_ref', label: '交接对象引用（不要填写密钥）' }, { name: 'audience_email', label: '限定领取邮箱（可选）', type: 'email', required: false }
            ], (values, key) => cdkRequest('/admin/distributions', { ...values, card_id: row.id, version: row.version }, { key }))}>分发</button>}
        </>}
        {tab === 'approvals' && row.state === 'pending' && can('approve') && row.maker_id !== staff.id && <>
            <button className={primaryClass} onClick={() => { if (window.confirm('已独立核对操作目标、原因及证据，确认批准执行？')) perform(async () => { await cdkRequest('/admin/approvals/' + row.id + '/decide', { version: row.version, approved: true }); await load(); }); }}>批准执行</button>
            <button className={buttonClass} onClick={() => perform(async () => { await cdkRequest('/admin/approvals/' + row.id + '/decide', { version: row.version, approved: false }); await load(); })}>拒绝</button>
        </>}
        {tab === 'distributions' && can('distribute') && !row.claimed_at && <button className={buttonClass} onClick={() => perform(async () => {
            setSecretTicket(await cdkRequest('/admin/distributions/' + row.id + '/ticket', {})); clearTimeout(ticketTimer.current); ticketTimer.current = setTimeout(() => setSecretTicket(null), 60000);
        })}>查看领取票据</button>}
        {tab === 'jobs' && row.kind === 'export' && can('export') && <button className={buttonClass} onClick={() => perform(() => downloadCdkExport(row.id))}>下载导出文件</button>}
        {tab === 'redemptions' && <>
            {can('control') && <button className={buttonClass} onClick={() => perform(async () => { await cdkRequest('/admin/redemptions/' + row.id + '/refresh', {}); await load(); })}>核对上游状态</button>}
            {can('reconcile') && row.state === 'unknown' && <button className={buttonClass} onClick={() => requestApproval('release', row)}>申请证据释放</button>}
            {can('reconcile') && row.mutation_state === 'unknown' && <button className={buttonClass} onClick={() => requestApproval('resolve_mutation', row)}>确认上次操作未执行</button>}
            {can('control') && ['processing', 'succeeded'].includes(row.state) && <button className={buttonClass} onClick={() => {
                const action = row.state === 'processing' ? 'recall' : 'close';
                if (window.confirm(action === 'recall' ? '申请撤回后仍需证据确认未消费，确定继续？' : '关闭任务不会恢复卡密，确定继续？')) perform(async () => {
                    await cdkRequest('/admin/redemptions/' + row.id + '/' + action, { version: row.version, confirmed: true }); await load();
                });
            }}>{row.state === 'processing' ? '申请撤回' : '关闭任务'}</button>}
        </>}
        {tab === 'channels' && can('catalog') && <button className={buttonClass} onClick={() => perform(async () => { await cdkRequest('/admin/channels/' + row.id + '/update', { version: row.version, enabled: !row.enabled }); await load(); })}>{row.enabled ? '停用渠道' : '启用渠道'}</button>}
        {tab === 'users' && can('iam') && row.id !== staff.id && <button className={buttonClass} onClick={() => perform(async () => { await cdkRequest('/admin/users/' + row.id + '/update', { version: row.version, enabled: !row.enabled }); await load(); })}>{row.enabled ? '停用员工' : '启用员工'}</button>}
        {tab === 'users' && can('iam') && row.id !== staff.id && <button className={buttonClass} onClick={() => openForm('调整员工权限', [
            { name: 'role', label: '角色', value: row.role, options: ['admin', 'operator', 'reviewer', 'support', 'auditor'].map(value => ({ value, label: value })) },
            { name: 'scope', label: '渠道范围', value: row.scopes[0], options: [{ value: '*', label: '全局范围' }, ...options(channels)] },
            { name: 'password', label: '重置密码（可选，至少 12 字符）', type: 'password', required: false }
        ], ({ scope, ...values }, key) => cdkRequest('/admin/users/' + row.id + '/update', { ...values, scopes: [scope], version: row.version }, { key }))}>调整权限或密码</button>}
        {tab === 'customers' && can('iam') && <button className={buttonClass} onClick={() => perform(async () => { await cdkRequest('/admin/customers/' + row.id + '/update', { version: row.version, enabled: !row.enabled }); await load(); })}>{row.enabled ? '停用客户' : '启用客户'}</button>}
    </div>;

    const tabs = [['batches', '发行批次'], ['cards', '卡密'], ['stocks', '供应商库存', 'stock'], ['benefits', '权益'], ['channels', '渠道'],
        ['distributions', '分发'], ['approvals', '审批'], ['jobs', '导入与导出'], ['redemptions', '核销订单'], ['audits', '操作审计', 'audit'], ['users', '员工权限', 'iam'], ['customers', '客户管理', 'iam']];

    if (!staff) return <main className={`${embedded ? 'py-8' : 'min-h-screen'} bg-slate-50 text-slate-900 flex items-center justify-center p-6`}><form onSubmit={event => { event.preventDefault(); perform(async () => {
        const result = await cdkRequest('/admin/session/login', { username, password }); setPassword(''); setCdkCsrf(result.csrf_token); setStaff(result);
    }); }} className="bg-white border rounded-xl p-8 w-full max-w-md space-y-4"><h1 className="font-bold text-xl">Chat GPT充值中心 · 卡密工作台</h1><p className="text-sm text-slate-600">使用具名充值员工账号登录</p>
        {notice && <p role="alert" className="text-red-700">{notice}</p>}
        <label className="block">用户名<input className={fieldClass} autoComplete="username" required value={username} onChange={event => setUsername(event.target.value)} /></label>
        <label className="block">密码<input className={fieldClass} type="password" autoComplete="current-password" required value={password} onChange={event => setPassword(event.target.value)} /></label>
        <button className={primaryClass} disabled={busy}>登录卡密工作台</button><p className="text-sm text-slate-600">使用充值员工账号管理 CDK；原管理员密码用于原充值订单和邮箱管理。</p><p><a href="/admin?tab=orders">进入原充值订单</a></p>
    </form></main>;

    return <div className={`${embedded ? '' : 'min-h-screen'} bg-slate-50 text-slate-900`}><header className="bg-white border-b px-6 py-4 flex justify-between flex-wrap gap-3"><div><h2 className="font-bold text-xl">CDK 卡密系统</h2><p className="text-sm">{staff.username} · {staff.role}</p></div><div className="flex flex-wrap gap-3 items-center"><a href="/recharge">前端 CDK 充值</a>{!embedded && <a href="/admin?tab=orders">原充值订单</a>}<button className={buttonClass} onClick={() => perform(async () => { await cdkRequest('/admin/session/logout', {}); setStaff(null); setCdkCsrf(''); setSecretTicket(null); })}>退出登录</button></div></header>
        <main className="max-w-[1600px] mx-auto p-5 space-y-5">
            {notice && <p role="alert" className="bg-amber-50 border border-amber-300 p-3 rounded-lg">{notice}</p>}
            {configuration?.fulfillment_enabled === false && <p role="status" className="border border-amber-300 bg-amber-50 p-4 rounded-lg">充值通道尚未启用，暂不能验证供应商库存或完成充值。可以先配置渠道和权益；生成 CDK 需要已验证的可用库存。</p>}
            {can('issue') && tab === 'batches' && <section aria-label="CDK 生成流程" className="bg-white border rounded-xl p-5 space-y-3">
                <h2 className="font-semibold">生成 CDK 并交付用户</h2>
                <ol className="grid sm:grid-cols-2 lg:grid-cols-4 gap-3 text-sm">
                    <li>1. 配置渠道和充值权益</li><li>2. 导入并验证供应商库存</li><li>3. 新建批次，生成 CDK 卡密</li><li>4. 复核激活、分发并导出</li>
                </ol>
                <p className="text-sm text-slate-600">每张 CDK 对应一份库存，单批最多 100 张。用户在充值首页输入生成的 GM1 卡密，确认账号后兑换。</p>
            </section>}
            {summary && <section aria-label="卡密统计" className="grid grid-cols-2 md:grid-cols-4 gap-3">{[['核销成功', summary.redemptions.succeeded || 0], ['待核对', summary.redemptions.unknown || 0], ['处理中', summary.redemptions.processing || 0], ['卡密总量', summary.cards.reduce((total, item) => total + item.count, 0)]].map(([label, count]) => <div className="border rounded-xl bg-white p-4" key={label}><p className="text-sm text-slate-500">{label}</p><strong className="text-2xl">{count}</strong></div>)}</section>}
            <nav className="flex flex-wrap gap-2" aria-label="卡密管理导航">{tabs.filter(([, , capability]) => !capability || can(capability)).map(([name, label]) => <button className={tab === name ? primaryClass : buttonClass} key={name} disabled={busy} onClick={() => setTab(name)}>{label}</button>)}</nav>
            <section className="bg-white border rounded-xl p-5 space-y-4">{toolbar()}
                {preview && tab === 'stocks' && <div className="p-4 border border-amber-300 rounded-lg space-y-3"><h2 className="font-bold">导入预览：有效 {preview.valid_count} 行，异常 {preview.error_count} 行</h2><ul>{preview.rows.filter(row => row.error_code).map(row => <li key={row.row}>第 {row.row} 行：{row.error_code}（尾号 {row.last4}）</li>)}</ul>
                    <button className={primaryClass} disabled={busy || !preview.valid_count} onClick={() => perform(async () => { await cdkRequest('/admin/imports/' + preview.id + '/commit', { version: 1, accept_valid_rows: true }, { key: preview.id }); setPreview(null); await load(); })}>确认导入有效行</button><button className={buttonClass} onClick={() => setPreview(null)}>取消预览</button>
                </div>}
                {secretTicket && <div className="border border-amber-300 p-4 rounded-lg"><p>领取票据（60 秒后隐藏，请通过受控渠道交付）</p><code className="break-all select-all">{secretTicket.ticket}</code><button className={buttonClass} onClick={() => setSecretTicket(null)}>立即隐藏</button></div>}
                <div className="overflow-x-auto"><table className="w-full text-sm text-left"><thead><tr className="border-b"><th className="p-3">对象</th><th className="p-3">状态与范围</th><th className="p-3">详细信息</th><th className="p-3">操作</th></tr></thead><tbody>
                    {items.map(row => <tr key={row.id} className="border-b align-top">
                        <td className="p-3 max-w-xs break-all">{tab === 'cards' && can('export') && <input aria-label={'选择卡密 ' + row.id} type="checkbox" checked={selection.includes(row.id)} onChange={event => setSelection(previous => event.target.checked ? [...previous, row.id] : previous.filter(value => value !== row.id))} />}<strong className="block">{row.masked_code || row.name || row.username || row.email || row.task_no || actionLabels[row.action] || row.action || row.kind || row.last4}</strong><span className="text-xs text-slate-500 select-all">{row.id}</span></td>
                        <td className="p-3"><p>{[row.state, row.control, row.usage, row.distribution].filter(Boolean).map(value => stateLabels[value] || value).join(' · ')}</p>{row.enabled !== undefined && <p>{row.enabled ? '启用' : '停用'}</p>}<p className="break-all">{row.channel_id || row.batch_id || row.card_id || row.role || ''}</p>{row.is_mock !== undefined && <p>{row.is_mock ? '模拟测试' : '正式履约'}</p>}</td>
                        <td className="p-3 max-w-sm break-words"><p>{row.reason || row.recipient_ref || row.purchase_ref || row.benefit?.name || row.plan_type || ''}</p>{row.quantity && <p>数量：{row.quantity}</p>}{row.target_id && <p className="break-all">操作目标：{row.target_id}</p>}{row.parameters?.reference && <p>证据引用：{row.parameters.reference}</p>}{row.parameters?.evidence_sha256 && <p className="break-all">证据摘要：{row.parameters.evidence_sha256}</p>}{row.parameters?.cards && <p>导出 {row.parameters.cards.length} 张</p>}<p>{row.expires_at || row.valid_until ? '到期：' + formatTime(row.expires_at || row.valid_until) : ''}</p>{row.result?.export_id && <p className="select-all">导出编号：{row.result.export_id}</p>}{tab === 'audits' && <p className="text-xs break-all">{JSON.stringify(row.details)}</p>}</td>
                        <td className="p-3"><fieldset disabled={busy}>{controls(row)}</fieldset></td>
                    </tr>)}
                </tbody></table>{items.length === 0 && <p className="text-slate-500 py-6 text-center">暂无记录</p>}</div>
                {cursor && <button disabled={busy} className={buttonClass} onClick={() => perform(() => load(cursor, true))}>加载更多</button>}
            </section>
        </main>
        {form && <FormDialog key={form.id} form={form} busy={busy} onClose={() => setForm(null)} onSubmit={values => perform(async () => { await form.submit(values, form.id); setForm(null); await load(); })} />}
    </div>;
}
