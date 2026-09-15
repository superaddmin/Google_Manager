import React, { useEffect, useState } from 'react';
import { Archive, Check, ExternalLink, Mail, RefreshCw } from 'lucide-react';
import api from '../services/api';

const GmailInboxView = ({ darkMode }) => {
    const [connections, setConnections] = useState([]);
    const [connectionId, setConnectionId] = useState('');
    const [messages, setMessages] = useState([]);
    const [selected, setSelected] = useState(null);
    const [query, setQuery] = useState('');
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState('');

    const loadConnections = async () => {
        const result = await api.getGmailConnections();
        setConnections(result.data || []);
        if (!connectionId && result.data?.length) setConnectionId(String(result.data[0].id));
    };

    const loadMessages = async () => {
        if (!connectionId) return;
        setLoading(true); setError('');
        try {
            const result = await api.getGmailMessages(connectionId, query);
            setMessages(result.data?.messages || []);
        } catch (loadError) { setError(loadError.message); }
        finally { setLoading(false); }
    };

    useEffect(() => { loadConnections().catch(loadError => setError(loadError.message)); }, []);
    useEffect(() => { loadMessages(); }, [connectionId]);

    const authorize = async () => {
        const result = await api.startGmailOAuth();
        window.location.href = result.data.authorizationUrl;
    };

    const openMessage = async message => {
        const result = await api.getGmailMessage(connectionId, message.id);
        setSelected(result.data);
    };

    const modify = async (message, action) => {
        if (action === 'read') await api.markGmailMessageRead(connectionId, message.id);
        if (action === 'archive') await api.archiveGmailMessage(connectionId, message.id);
        await loadMessages();
        if (selected?.id === message.id) setSelected(null);
    };

    const panel = darkMode ? 'bg-slate-800 border-slate-700 text-slate-100' : 'bg-white border-slate-200 text-slate-900';
    return <div className="space-y-6">
        <div className="flex flex-wrap items-center justify-between gap-3">
            <div><h1 className="text-2xl font-bold">Gmail 收件箱</h1><p className="text-sm opacity-70">通过 Gmail API 管理已授权邮箱</p></div>
            <div className="flex gap-2">
                <button onClick={authorize} className="px-4 py-2 rounded-lg bg-blue-600 text-white"><ExternalLink size={16} className="inline mr-2" />授权 Gmail</button>
                <button onClick={() => { loadConnections(); loadMessages(); }} className="px-3 py-2 rounded-lg border"><RefreshCw size={16} /></button>
            </div>
        </div>
        <div className={`rounded-xl border p-4 ${panel}`}>
            <div className="flex flex-wrap gap-3">
                <select value={connectionId} onChange={event => setConnectionId(event.target.value)} className="rounded-lg border px-3 py-2 bg-transparent">
                    <option value="">选择已授权邮箱</option>{connections.map(item => <option key={item.id} value={item.id}>{item.email}</option>)}
                </select>
                <input value={query} onChange={event => setQuery(event.target.value)} onKeyDown={event => event.key === 'Enter' && loadMessages()} placeholder="搜索 Gmail，例如 from:github.com" className="flex-1 min-w-[240px] rounded-lg border px-3 py-2 bg-transparent" />
                <button onClick={loadMessages} disabled={!connectionId || loading} className="px-4 py-2 rounded-lg bg-slate-700 text-white">搜索</button>
            </div>
            {error && <p className="mt-3 text-red-500">{error}</p>}
        </div>
        {!connections.length && <div className={`rounded-xl border p-8 text-center ${panel}`}><Mail className="mx-auto mb-3" /><p>尚未授权 Gmail 账号，请先点击“授权 Gmail”。</p></div>}
        <div className="grid gap-6 lg:grid-cols-[1fr_1fr]">
            <div className={`rounded-xl border divide-y ${panel}`}>{messages.map(message => <div key={message.id} className="p-4 flex gap-3 items-start"><button className="text-left flex-1" onClick={() => openMessage(message)}><div className="font-semibold">{message.subject || '(无主题)'}</div><div className="text-sm opacity-70 truncate">{message.from}</div><div className="text-sm opacity-70 truncate">{message.snippet}</div></button><div className="flex gap-1"><button title="标记已读" onClick={() => modify(message, 'read')} className="p-2 rounded hover:bg-slate-500/20"><Check size={16} /></button><button title="归档" onClick={() => modify(message, 'archive')} className="p-2 rounded hover:bg-slate-500/20"><Archive size={16} /></button></div></div>)}{!loading && connectionId && !messages.length && <p className="p-8 text-center opacity-70">收件箱暂无匹配邮件</p>}</div>
            <div className={`rounded-xl border p-5 min-h-[240px] ${panel}`}>{selected ? <><h2 className="text-xl font-bold">{selected.subject || '(无主题)'}</h2><p className="mt-2 text-sm opacity-70">{selected.from} · {selected.date}</p><pre className="mt-5 whitespace-pre-wrap text-sm font-sans">{selected.body || selected.snippet}</pre></> : <p className="text-center opacity-60 mt-16">选择邮件查看详情</p>}</div>
        </div>
    </div>;
};

export default GmailInboxView;