import React, { useEffect, useRef, useState } from 'react';
import { cdkRequest, normalizePlatformCode, requestKey } from '../services/cdk';

const fieldClass = 'w-full rounded-lg border border-slate-300 p-3 bg-white text-slate-900';
const buttonClass = 'rounded-lg bg-emerald-700 text-white px-4 py-2 disabled:opacity-40';
const labels = { prepared: '已受理', dispatching: '提交中', processing: '履约处理中', unknown: '待核对，请勿重复下单',
    succeeded: '已完成', released: '已确认未消费，可重新兑换', cancelled: '尚未提交，已取消占用' };

export default function CdkPortal({ embedded = false }) {
    const [configuration, setConfiguration] = useState(null);
    const [code, setCode] = useState('');
    const [card, setCard] = useState(null);
    const [token, setToken] = useState('');
    const [email, setEmail] = useState('');
    const [confirmed, setConfirmed] = useState(false);
    const [renewal, setRenewal] = useState(false);
    const [records, setRecords] = useState([]);
    const [ownedCards, setOwnedCards] = useState([]);
    const [result, setResult] = useState(null);
    const [notice, setNotice] = useState('');
    const [busy, setBusy] = useState(false);
    const pending = useRef(false);
    const [customer, setCustomer] = useState(null);
    const [loginEmail, setLoginEmail] = useState('');
    const [otp, setOtp] = useState('');
    const [otpChallenge, setOtpChallenge] = useState(null);
    const [ticket, setTicket] = useState('');
    const [key, setKey] = useState(() => { try { return sessionStorage.getItem('cdk-request-key') || ''; } catch { return ''; } });

    useEffect(() => {
        if (!embedded) document.title = 'Chat GPT充值中心 · 平台卡密兑换';
        const controller = new AbortController();
        cdkRequest('/config', undefined, { signal: controller.signal }).then(setConfiguration).catch(error => { if (!controller.signal.aborted) setNotice(error.message); });
        cdkRequest('/customer/me', undefined, { signal: controller.signal }).then(setCustomer).catch(() => {});
        return () => controller.abort();
    }, [embedded]);

    const loadRecords = async () => {
        const data = await cdkRequest('/redemptions/mine');
        setRecords(data.items);
        if (customer) setOwnedCards((await cdkRequest('/cards/mine')).items);
    };

    const perform = async action => {
        if (pending.current) return;
        pending.current = true;
        setBusy(true);
        setNotice('');
        try { await action(); } catch (error) { setNotice(error.message); }
        finally { pending.current = false; setBusy(false); }
    };

    const submit = event => {
        event.preventDefault();
        perform(async () => {
            const normalized = normalizePlatformCode(code);
            const parsed = JSON.parse(token);
            if (!parsed.accessToken || parsed.user?.email?.trim().toLowerCase() !== email.trim().toLowerCase()) throw new Error('目标邮箱必须与 Session JSON 的 user.email 一致');
            const currentKey = key || requestKey();
            setKey(currentKey);
            try { sessionStorage.setItem('cdk-request-key', currentKey); } catch {}
            const payload = { code: normalized, token_input: token, plan_type: card.benefit.plan_type, account_email: email,
                is_renewal: renewal, agreement_accepted: confirmed, acknowledge_non_free: confirmed };
            const challenge = await cdkRequest('/challenges', payload);
            try {
                const submitted = await cdkRequest('/redemptions', { ...payload, challenge_token: challenge.challenge_token }, { key: currentKey, timeoutMs: 45000 });
                setResult(submitted);
                setToken('');
                await loadRecords();
            } catch (error) {
                try { setResult(await cdkRequest('/redemptions/recover', { idempotency_key: currentKey })); setToken(''); }
                catch { throw new Error(error.message + '。请保留本页请求编号并点击“找回提交结果”，不要更换请求重发。'); }
            }
        });
    };

    const showRecord = record => <article key={record.id} className="rounded-lg border p-4 space-y-2">
        <p className="font-semibold">{labels[record.state] || record.state}</p>
        <p className="text-sm break-all">任务编号：{record.task_no}</p>
        <p className="text-sm">{record.task.plan_type} · {record.task.is_mock ? '模拟测试' : '真实履约'} · {record.task.status_text}</p>
        <p className="text-xs text-slate-500">本地记录更新于 {new Date(record.updated_at * 1000).toLocaleString()}</p>
        <div className="flex gap-3 flex-wrap">
            <button type="button" className={buttonClass} disabled={busy} onClick={() => perform(async () => {
                setResult(await cdkRequest('/redemptions/' + record.id + '/refresh', {})); await loadRecords();
            })}>查询最新状态</button>
            {record.state === 'processing' && <button type="button" disabled={busy} className={buttonClass} onClick={() => {
                if (window.confirm('确认申请撤回？撤回后仍需确认供应商未消费，卡密不会立即恢复。')) perform(async () => {
                    setResult(await cdkRequest('/redemptions/' + record.id + '/recall', { confirmed: true, version: record.version })); await loadRecords();
                });
            }}>申请撤回</button>}
            {record.task.status === 'completed' && <button type="button" disabled={busy} className={buttonClass} onClick={() => {
                if (window.confirm('关闭任务不会恢复卡密或退还权益，确定继续？')) perform(async () => {
                    setResult(await cdkRequest('/redemptions/' + record.id + '/close', { confirmed: true, version: record.version })); await loadRecords();
                });
            }}>关闭任务</button>}
        </div>
    </article>;

    return <div className={`${embedded ? 'rounded-xl' : 'min-h-screen'} bg-slate-50 text-slate-900`}>
        {!embedded && <header className="bg-white border-b px-6 py-5 flex justify-between flex-wrap gap-3"><h1 className="text-xl font-bold">Chat GPT充值中心 · 平台卡密兑换</h1><a href="/recharge" className="text-emerald-700">返回充值中心</a></header>}
        <section aria-label="CDK 卡密兑换" className={`max-w-4xl mx-auto ${embedded ? 'p-3 sm:p-5' : 'p-6'} space-y-6`}>
            {notice && <p role="alert" className="rounded-lg bg-amber-50 border border-amber-300 p-4">{notice}</p>}
            {configuration && !configuration.enabled && <p role="status">平台卡密功能尚未启用，请联系运营人员。</p>}
            {configuration?.enabled && <>
                {configuration.fulfillment_enabled === false && <p role="status" className="rounded-lg bg-amber-50 border border-amber-300 p-4">充值通道暂未开放，目前可以查看卡密和历史记录，暂不能提交充值。</p>}
                {configuration.claim_enabled && <section className="bg-white p-5 rounded-xl border space-y-3">
                    <h2 className="font-semibold">邮箱登录与领取</h2>
                    {customer ? <div className="flex justify-between"><p>已验证：{customer.email}</p><button className="text-emerald-700" disabled={busy} onClick={() => perform(async () => {
                        await cdkRequest('/customer/logout', {}); setCustomer(null); setRecords([]); setOwnedCards([]); setResult(null);
                    })}>退出</button></div> : <>
                        <label className="block">登录邮箱<input className={fieldClass} type="email" value={loginEmail} onChange={event => setLoginEmail(event.target.value)} /></label>
                        <button disabled={busy} className={buttonClass} onClick={() => perform(async () => { setOtpChallenge(await cdkRequest('/customer/otp/send', { email: loginEmail })); setNotice('验证码已发送，5 分钟内有效。'); })}>发送验证码</button>
                        {otpChallenge && <div className="flex gap-3"><input aria-label="邮箱验证码" className={fieldClass} inputMode="numeric" maxLength={6} value={otp} onChange={event => setOtp(event.target.value)} /><button className={buttonClass} disabled={busy} onClick={() => perform(async () => {
                            setCustomer(await cdkRequest('/customer/otp/verify', { challenge_id: otpChallenge.challenge_id, code: otp })); setOtp(''); setOtpChallenge(null); setRecords([]); setResult(null);
                        })}>验证登录</button></div>}
                    </>}
                    {customer && <div className="flex gap-3"><input aria-label="领取票据" className={fieldClass} autoComplete="off" placeholder="输入运营提供的领取票据" value={ticket} onChange={event => setTicket(event.target.value)} /><button disabled={busy} className={buttonClass} onClick={() => perform(async () => {
                        await cdkRequest('/claims', { ticket }); setTicket(''); setNotice('领取成功，可在我的卡密中查看。'); await loadRecords();
                    })}>领取卡密</button></div>}
                </section>}
                <form className="bg-white p-5 rounded-xl border space-y-4" onSubmit={submit}>
                    <h2 className="font-semibold text-lg">CDK 卡密充值</h2>
                    <p className="text-sm text-slate-600">输入运营提供的 GM1 卡密，验证权益后确认充值账号，即可提交兑换。</p>
                    <label className="block">平台卡密<input className={fieldClass} autoComplete="off" maxLength={64} required placeholder="GM1…" value={code} onChange={event => { setCode(event.target.value); setCard(null); setConfirmed(false); }} /></label>
                    <button type="button" className={buttonClass} disabled={busy} onClick={() => perform(async () => { setCard(await cdkRequest('/validate', { code: normalizePlatformCode(code) })); })}>验证卡密</button>
                    {card && <>
                        <div className="bg-emerald-50 p-3 rounded-lg"><strong>{card.benefit.name}</strong><p>{card.masked_code} · {card.is_mock ? '模拟测试卡' : '正式卡'}</p><p>有效至 {new Date(card.expires_at * 1000).toLocaleString()}</p></div>
                        <label className="block">Session JSON<textarea className={fieldClass} rows={5} required maxLength={65535} autoComplete="off" spellCheck={false} value={token} onChange={event => setToken(event.target.value)} /></label>
                        <label className="block">目标账号邮箱<input className={fieldClass} type="email" required value={email} onChange={event => setEmail(event.target.value)} /></label>
                        {card.benefit.renewal_allowed && <label className="flex gap-2"><input type="checkbox" checked={renewal} onChange={event => setRenewal(event.target.checked)} />续费现有订阅</label>}
                        <label className="flex items-start gap-2"><input type="checkbox" required checked={confirmed} onChange={event => setConfirmed(event.target.checked)} /><span>已核对目标账号，同意充值服务协议和套餐覆盖规则；确认消费本卡对应的一份权益。</span></label>
                        <button className={buttonClass} disabled={busy || !confirmed || configuration.fulfillment_enabled === false}>确认核销</button>
                    </>}
                </form>
                {key && <section className="border rounded-xl p-4 space-y-3"><p className="text-sm break-all">本次请求编号：{key}</p><div className="flex gap-3">
                    <button disabled={busy} className={buttonClass} onClick={() => perform(async () => { setResult(await cdkRequest('/redemptions/recover', { idempotency_key: key })); })}>找回提交结果</button>
                    {result && <button disabled={busy} className={buttonClass} onClick={() => { setKey(''); setCode(''); setCard(null); setToken(''); setResult(null); setConfirmed(false); try { sessionStorage.removeItem('cdk-request-key'); } catch {} }}>开始另一笔兑换</button>}
                </div></section>}
                {result && <section aria-label="本次兑换结果">{showRecord(result)}</section>}
                <section className="space-y-3"><div className="flex justify-between"><h2 className="font-semibold">我的核销记录与卡密</h2><button disabled={busy} className={buttonClass} onClick={() => perform(loadRecords)}>加载记录</button></div>
                    {!customer && <p className="text-sm text-slate-500">匿名兑换记录仅限创建请求的浏览器会话查看。</p>}
                    {records.map(showRecord)}
                    {ownedCards.map(owned => <div className="border p-3 rounded-lg flex justify-between flex-wrap gap-3" key={owned.id}><span>{owned.masked_code} · {owned.benefit.name} · {owned.usage}</span><button disabled={busy} className={buttonClass} onClick={() => perform(async () => {
                        const revealed = await cdkRequest('/cards/' + owned.id + '/reveal', {}); setCode(revealed.code); setCard(null); setNotice('卡密已填入兑换输入框。');
                    })}>填入兑换框</button></div>)}
                </section>
            </>}
        </section>
    </div>;
}
