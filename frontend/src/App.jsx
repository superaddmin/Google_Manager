import React, { useState, useEffect, useMemo, useRef } from 'react';
import {
    Users,
    UserPlus,
    ShieldCheck,
    ShieldAlert,
    X,
    CheckCircle2,
    AlertTriangle,
    Moon,
    Sun,
    LogOut,
    MailCheck,
    BarChart3,
    CreditCard
} from 'lucide-react';

// 导入服务和组件
import api from './services/api';
import AccountListView from './components/AccountListView';
import ImportView from './components/ImportView';
import LoginPage from './components/LoginPage';
import GooglemailView from './components/GooglemailView';
import DashboardView from './components/DashboardView';
import GmailInboxView from './components/GmailInboxView';
import SecurityCenterView from './components/SecurityCenterView';
import RechargeView from './components/RechargeView';

const App = () => {
    const [view, setView] = useState('list');
    const [accounts, setAccounts] = useState([]);
    const [search, setSearch] = useState('');
    const [notification, setNotification] = useState(null);
    const [loading, setLoading] = useState(true);

    // Modals state
    const [editingAccount, setEditingAccount] = useState(null);
    const [deletingId, setDeletingId] = useState(null);
    const [twoFACode, setTwoFACode] = useState({ id: null, code: '', expiry: 0 });
    const accountsRequestRef = useRef(0);
    const notificationTimerRef = useRef(null);

    const [isLoggedIn, setIsLoggedIn] = useState(false);

    // 暗色模式状态
    const [darkMode, setDarkMode] = useState(() => {
        try {
            return JSON.parse(localStorage.getItem('darkMode')) === true;
        } catch {
            return false;
        }
    });

    // 保存暗色模式设置到 localStorage 并同步 documentElement class
    useEffect(() => {
        try {
            localStorage.setItem('darkMode', JSON.stringify(darkMode));
        } catch {}
        if (typeof document !== 'undefined') {
            document.documentElement.classList.toggle('dark', Boolean(darkMode));
        }
    }, [darkMode]);

    // --- 加载账号数据 ---
    useEffect(() => {
        if (isLoggedIn) {
            loadAccounts();
        } else {
            accountsRequestRef.current += 1;
            setAccounts([]);
            setLoading(false);
        }
    }, [isLoggedIn]);

    const loadAccounts = async () => {
        const requestId = ++accountsRequestRef.current;
        try {
            setLoading(true);
            const data = await api.getAccounts();
            if (requestId !== accountsRequestRef.current) return;
            setAccounts(data);
        } catch (error) {
            if (requestId !== accountsRequestRef.current) return;
            console.error('加载账号失败:', error);
            if (error.status === 401) {
                setIsLoggedIn(false);
                showNotification('登录状态已失效，请重新登录', 'error');
                return;
            }
            showNotification('加载账号失败', 'error');
        } finally {
            if (requestId === accountsRequestRef.current) setLoading(false);
        }
    };

    // --- 2FA Countdown Logic ---
    useEffect(() => {
        let timer;
        if (twoFACode.expiry > 0) {
            timer = setInterval(() => {
                setTwoFACode(prev => ({
                    ...prev,
                    expiry: prev.expiry - 1
                }));
            }, 1000);
        } else if (twoFACode.expiry === 0 && twoFACode.id !== null) {
            setTwoFACode({ id: null, code: '', expiry: 0 });
        }
        return () => clearInterval(timer);
    }, [twoFACode.expiry, twoFACode.id]);

    // --- Helpers ---
    const showNotification = (msg, type = 'success') => {
        if (notificationTimerRef.current) {
            clearTimeout(notificationTimerRef.current);
        }
        setNotification({ msg, type });
        notificationTimerRef.current = setTimeout(() => {
            setNotification(null);
            notificationTimerRef.current = null;
        }, 3000);
    };

    const copyToClipboard = async (text, label) => {
        try {
            if (navigator.clipboard && window.isSecureContext) {
                await navigator.clipboard.writeText(text);
            } else {
                const el = document.createElement('textarea');
                el.value = text;
                document.body.appendChild(el);
                el.select();
                const copied = document.execCommand('copy');
                document.body.removeChild(el);
                if (!copied) throw new Error('copy command failed');
            }
            showNotification(`已复制 ${label} 到剪切板`);
        } catch (error) {
            console.error('复制失败:', error);
            showNotification(`复制 ${label} 失败`, 'error');
        }
    };

    const generate2FA = async (id, secret) => {
        try {
            const result = await api.get2FACode(id);
            if (result.success) {
                setTwoFACode({ id, code: result.data.code, expiry: result.data.expiry });
                showNotification('2FA 验证码已刷新');
            } else {
                setTwoFACode({ id: null, code: '', expiry: 0 });
                showNotification(result.message || '获取 2FA 验证码失败', 'error');
            }
        } catch (error) {
            console.error('获取 2FA 验证码失败:', error);
            setTwoFACode({ id: null, code: '', expiry: 0 });
            showNotification('获取 2FA 验证码失败', 'error');
        }
    };

    const toggleStatus = async (id) => {
        try {
            const result = await api.toggleStatus(id);
            if (result.success) {
                setAccounts(current => current.map(acc =>
                    acc.id === id ? { ...acc, status: result.data.status } : acc
                ));
            } else {
                showNotification(result.message || '切换状态失败', 'error');
            }
        } catch (error) {
            console.error('切换状态失败:', error);
            showNotification('切换状态失败', 'error');
        }
    };

    const toggleSoldStatus = async (id, currentStatus) => {
        if (currentStatus === 'sold') {
            const confirmed = window.confirm('确定要将该账号标记为"未售出"吗？\n\n这将撤销之前的售出记录。');
            if (!confirmed) {
                return;
            }
        }

        try {
            const result = await api.toggleSoldStatus(id);
            if (result.success) {
                setAccounts(current => current.map(acc =>
                    acc.id === id ? { ...acc, soldStatus: result.data.soldStatus } : acc
                ));
                const status = result.data.soldStatus === 'sold' ? '已售出' : '未售出';
                showNotification(`账号已标记为${status}`);
            } else {
                showNotification(result.message || '切换出售状态失败', 'error');
            }
        } catch (error) {
            console.error('切换出售状态失败:', error);
            showNotification('切换出售状态失败', 'error');
        }
    };

    // --- Handlers ---
    const handleDelete = async () => {
        try {
            const result = await api.deleteAccount(deletingId);
            if (result.success) {
                setAccounts(current => current.filter(acc => acc.id !== deletingId));
                showNotification('账号已删除');
            } else {
                showNotification(result.message || '删除失败', 'error');
            }
        } catch (error) {
            console.error('删除失败:', error);
            showNotification('删除失败', 'error');
        }
        setDeletingId(null);
    };

    // --- 批量操作 ---
    const handleBatchDelete = async (accountIds) => {
        const confirmed = window.confirm(`确定要批量删除选中的 ${accountIds.length} 个账号吗？\n\n此操作不可撤销，相关修改历史将一并移除。`);
        if (!confirmed) return false;
        try {
            const result = await api.batchDeleteAccounts(accountIds);
            if (result.success) {
                await loadAccounts();
                showNotification(result.message || '批量删除成功');
                return true;
            }
            showNotification(result.message || '批量删除失败', 'error');
            return false;
        } catch (error) {
            console.error('批量删除失败:', error);
            showNotification(error.message || '批量删除失败', 'error');
            return false;
        }
    };

    const handleBatchSold = async (accountIds, status) => {
        const label = status === 'sold' ? '已售出' : '未售出';
        if (status === 'unsold') {
            const confirmed = window.confirm(`确定要将选中的 ${accountIds.length} 个账号标记为"未售出"吗？\n\n这将撤销之前的售出记录。`);
            if (!confirmed) return false;
        }
        try {
            const result = await api.batchSetSoldStatus(accountIds, status);
            if (result.success) {
                await loadAccounts();
                showNotification(`已将 ${result.data.updated_count} 个账号标记为${label}`);
                return true;
            }
            showNotification(result.message || '批量更新出售状态失败', 'error');
            return false;
        } catch (error) {
            console.error('批量更新出售状态失败:', error);
            showNotification(error.message || '批量更新出售状态失败', 'error');
            return false;
        }
    };

    const handleBatchRemark = async (accountIds, remark) => {
        try {
            const result = await api.batchSetRemark(accountIds, remark);
            if (result.success) {
                await loadAccounts();
                showNotification(`已更新 ${result.data.updated_count} 个账号的备注`);
                return true;
            }
            showNotification(result.message || '批量更新备注失败', 'error');
            return false;
        } catch (error) {
            console.error('批量更新备注失败:', error);
            showNotification(error.message || '批量更新备注失败', 'error');
            return false;
        }
    };

    const handleUpdate = async (e) => {
        e.preventDefault();
        const formData = new FormData(e.target);
        const updated = {
            email: formData.get('email'),
            password: formData.get('password'),
            recovery: formData.get('recovery'),
            secret: (formData.get('secret') || '').replace(/\s/g, ''),
            remark: formData.get('remark'),
        };

        try {
            const result = await api.updateAccount(editingAccount.id, updated);
            if (result.success) {
                setAccounts(current => current.map(acc =>
                    acc.id === editingAccount.id ? result.data : acc
                ));
                showNotification('账号信息已更新');
                setEditingAccount(null);
            } else {
                showNotification(result.message || '更新失败', 'error');
            }
        } catch (error) {
            console.error('更新失败:', error);
            showNotification('更新失败', 'error');
        }
    };

    const handleLogout = async () => {
        try {
            const result = await api.logout();
            if (!result.success) throw new Error('logout failed');
            setIsLoggedIn(false);
            setAccounts([]);
            setEditingAccount(null);
            setDeletingId(null);
            setTwoFACode({ id: null, code: '', expiry: 0 });
            setView('list');
        } catch (error) {
            console.error('退出登录失败:', error);
            showNotification('退出登录失败，请重试', 'error');
        }
    };

    const handleImport = async (importedList) => {
        try {
            const result = await api.batchImport(importedList);
            if (result.success) {
                const { success_count, failed_count, failed_emails } = result.data;
                if (success_count > 0) {
                    await loadAccounts();
                    setView('list');
                }
                if (failed_count > 0) {
                    const duplicateInfo = failed_emails.slice(0, 3).join('、');
                    const moreInfo = failed_emails.length > 3 ? `等${failed_emails.length}个` : '';
                    showNotification(
                        `${success_count > 0 ? `成功导入 ${success_count} 个账号` : '未新增账号'}，${failed_count} 个账号重复或格式无效：${duplicateInfo}${moreInfo}`,
                        success_count > 0 ? 'success' : 'error'
                    );
                } else {
                    showNotification(`成功导入 ${success_count} 个账号`);
                }
            } else {
                showNotification(result.message || '导入失败', 'error');
            }
        } catch (error) {
            console.error('导入失败:', error);
            showNotification(error.message || '导入失败', 'error');
        }
    };

    const filteredAccounts = useMemo(() => {
        return accounts.filter(acc =>
            acc.email.toLowerCase().includes(search.toLowerCase()) ||
            (acc.remark && acc.remark.toLowerCase().includes(search.toLowerCase()))
        );
    }, [accounts, search]);

    // 未登录时显示登录页面
    if (!isLoggedIn) {
        return <LoginPage onLoginSuccess={() => setIsLoggedIn(true)} darkMode={darkMode} />;
    }

    return (
        <div className={`min-h-screen transition-colors duration-300 ${darkMode ? 'bg-slate-900 text-slate-100' : 'bg-slate-50 text-slate-800'}`}>
            <nav className={`${darkMode ? 'bg-slate-800/90 border-slate-700/80' : 'bg-white/90 border-slate-200/80'} border-b sticky top-0 z-30 backdrop-blur-md transition-colors duration-300`}>
                <div className="max-w-[1600px] mx-auto px-4 sm:px-6 lg:px-8">
                    <div className="flex justify-between h-16 items-center">
                        <div className="flex items-center gap-3 flex-shrink-0">
                            <div className="bg-gradient-to-tr from-blue-600 to-indigo-600 p-2.5 rounded-xl shadow-md shadow-blue-500/20">
                                <ShieldCheck className="text-white w-5 h-5" />
                            </div>
                            <span
                                className={`hidden sm:inline-block text-xl font-bold tracking-tight bg-clip-text text-transparent bg-gradient-to-r ${darkMode ? 'from-blue-400 via-indigo-300 to-cyan-300' : 'from-blue-600 via-indigo-600 to-cyan-600'}`}>
                                GoogleManager
                            </span>
                        </div>

                        <div className="flex items-center gap-1.5 sm:gap-3 min-w-0">
                            <div className={`flex gap-1 ${darkMode ? 'bg-slate-800 border border-slate-700/60' : 'bg-slate-100/90 border border-slate-200/60'} p-1 rounded-xl shadow-inner`}>
                                <button onClick={() => setView('list')} title="账号列表" aria-label="账号列表"
                                    className={`flex items-center gap-2 px-3.5 py-1.5 rounded-lg text-sm transition-all ${view === 'list' ?
                                        (darkMode ? 'bg-slate-700 text-blue-400 shadow-sm font-semibold' : 'bg-white text-blue-600 shadow-sm font-semibold')
                                        : (darkMode ? 'text-slate-400 hover:text-slate-200 hover:bg-slate-700/50' : 'text-slate-600 hover:text-slate-900 hover:bg-white/60')}`}
                                >
                                    <Users size={16} />
                                    <span className="hidden sm:inline">账号列表</span>
                                </button>
                                <button onClick={() => setView('import')} title="导入账号" aria-label="导入账号"
                                    className={`flex items-center gap-2 px-3.5 py-1.5 rounded-lg text-sm transition-all ${view === 'import' ?
                                        (darkMode ? 'bg-slate-700 text-blue-400 shadow-sm font-semibold' : 'bg-white text-blue-600 shadow-sm font-semibold')
                                        : (darkMode ? 'text-slate-400 hover:text-slate-200 hover:bg-slate-700/50' : 'text-slate-600 hover:text-slate-900 hover:bg-white/60')}`}
                                >
                                    <UserPlus size={16} />
                                    <span className="hidden sm:inline">导入账号</span>
                                </button>
                                <button onClick={() => setView('googlemail')} title="Googlemail" aria-label="Googlemail"
                                    className={`flex items-center gap-2 px-3.5 py-1.5 rounded-lg text-sm transition-all ${view === 'googlemail' ?
                                        (darkMode ? 'bg-slate-700 text-cyan-400 shadow-sm font-semibold' : 'bg-white text-cyan-600 shadow-sm font-semibold')
                                        : (darkMode ? 'text-slate-400 hover:text-slate-200 hover:bg-slate-700/50' : 'text-slate-600 hover:text-slate-900 hover:bg-white/60')}`}
                                >
                                    <MailCheck size={16} />
                                    <span className="hidden lg:inline">Googlemail</span>
                                </button>
                                <button onClick={() => setView('gmail-inbox')} title="Gmail 收件箱" aria-label="Gmail 收件箱"
                                    className={`flex items-center gap-2 px-3.5 py-1.5 rounded-lg text-sm transition-all ${view === 'gmail-inbox' ?
                                        (darkMode ? 'bg-slate-700 text-indigo-400 shadow-sm font-semibold' : 'bg-white text-indigo-600 shadow-sm font-semibold')
                                        : (darkMode ? 'text-slate-400 hover:text-slate-200 hover:bg-slate-700/50' : 'text-slate-600 hover:text-slate-900 hover:bg-white/60')}`}
                                >
                                    <MailCheck size={16} />
                                    <span className="hidden lg:inline">Gmail 收件箱</span>
                                </button>
                                <button onClick={() => setView('dashboard')} title="统计看板" aria-label="统计看板"
                                    className={`flex items-center gap-2 px-3.5 py-1.5 rounded-lg text-sm transition-all ${view === 'dashboard' ?
                                        (darkMode ? 'bg-slate-700 text-blue-400 shadow-sm font-semibold' : 'bg-white text-blue-600 shadow-sm font-semibold')
                                        : (darkMode ? 'text-slate-400 hover:text-slate-200 hover:bg-slate-700/50' : 'text-slate-600 hover:text-slate-900 hover:bg-white/60')}`}
                                >
                                    <BarChart3 size={16} />
                                    <span className="hidden lg:inline">统计看板</span>
                                </button>
                                <button onClick={() => setView('security')} title="安全防盗" aria-label="安全防盗"
                                    className={`flex items-center gap-2 px-3.5 py-1.5 rounded-lg text-sm transition-all ${view === 'security' ?
                                        (darkMode ? 'bg-slate-700 text-rose-400 shadow-sm font-semibold' : 'bg-white text-rose-600 shadow-sm font-semibold')
                                        : (darkMode ? 'text-slate-400 hover:text-slate-200 hover:bg-slate-700/50' : 'text-slate-600 hover:text-slate-900 hover:bg-white/60')}`}
                                >
                                    <ShieldAlert size={16} />
                                    <span className="hidden lg:inline">安全防盗</span>
                                </button>
                                <button onClick={() => setView('recharge')} title="充值交付" aria-label="充值交付"
                                    className={`flex items-center gap-2 px-3.5 py-1.5 rounded-lg text-sm transition-all ${view === 'recharge' ?
                                        (darkMode ? 'bg-slate-700 text-emerald-400 shadow-sm font-semibold' : 'bg-white text-emerald-600 shadow-sm font-semibold')
                                        : (darkMode ? 'text-slate-400 hover:text-slate-200 hover:bg-slate-700/50' : 'text-slate-600 hover:text-slate-900 hover:bg-white/60')}`}
                                >
                                    <CreditCard size={16} />
                                    <span className="hidden lg:inline">充值交付</span>
                                </button>
                            </div>

                            {/* 暗色模式切换按钮 */}
                            <button
                                onClick={() => setDarkMode(!darkMode)}
                                className={`p-2.5 rounded-xl border transition-all ${darkMode
                                    ? 'bg-slate-800 border-slate-700 text-amber-400 hover:bg-slate-700'
                                    : 'bg-white border-slate-200 text-slate-600 hover:bg-slate-100 hover:text-slate-900'}`}
                                title={darkMode ? '切换亮色模式' : '切换暗色模式'}
                            >
                                {darkMode ? <Sun size={18} /> : <Moon size={18} />}
                            </button>

                            <button
                                onClick={handleLogout}
                                className={`p-2.5 rounded-xl border transition-all ${darkMode
                                    ? 'bg-slate-800 border-slate-700 text-slate-400 hover:text-rose-400 hover:bg-slate-700'
                                    : 'bg-white border-slate-200 text-slate-500 hover:text-rose-600 hover:bg-slate-100'}`}
                                title="退出登录"
                            >
                                <LogOut size={18} />
                            </button>
                        </div>
                    </div>
                </div>
            </nav>

            <main className="max-w-[1600px] mx-auto px-4 py-8">
                {view === 'list' ? (
                    <AccountListView
                        accounts={filteredAccounts}
                        search={search}
                        setSearch={setSearch}
                        copyToClipboard={copyToClipboard}
                        generate2FA={generate2FA}
                        twoFACode={twoFACode}
                        toggleStatus={toggleStatus}
                        toggleSoldStatus={toggleSoldStatus}
                        onEdit={setEditingAccount}
                        onDelete={setDeletingId}
                        onBatchDelete={handleBatchDelete}
                        onBatchSold={handleBatchSold}
                        onBatchRemark={handleBatchRemark}
                        loading={loading}
                        darkMode={darkMode}
                    />
                ) : view === 'import' ? (
                    <ImportView onImport={handleImport} onCancel={() => setView('list')} darkMode={darkMode} />
                ) : view === 'gmail-inbox' ? (
                    <GmailInboxView darkMode={darkMode} />
                ) : view === 'dashboard' ? (
                    <DashboardView darkMode={darkMode} />
                ) : view === 'security' ? (
                    <SecurityCenterView darkMode={darkMode} showNotification={showNotification} />
                ) : view === 'recharge' ? (
                    <RechargeView
                        accounts={accounts.map(acc => ({
                            id: acc.id,
                            email: acc.email,
                            remark: acc.remark,
                            status: acc.status
                        }))}
                        darkMode={darkMode}
                        showNotification={showNotification}
                    />
                ) : (
                    <GooglemailView
                        accounts={accounts}
                        darkMode={darkMode}
                        onTaskComplete={loadAccounts}
                    />
                )}
            </main>

            {/* Notification Toast */}
            {notification && (
                <div className={`fixed bottom-8 right-8 flex items-center gap-3 px-5 py-3.5 rounded-2xl shadow-xl transition-all
            animate-in slide-in-from-bottom-4 fade-in duration-300 z-[100] backdrop-blur-md border ${
                notification.type === 'success'
                    ? 'bg-emerald-600/95 text-white border-emerald-500/30 shadow-emerald-950/20'
                    : 'bg-red-600/95 text-white border-red-500/30 shadow-red-950/20'
            }`}>
                    <CheckCircle2 size={19} />
                    <span className="font-medium text-sm tracking-wide">{notification.msg}</span>
                </div>
            )}

            {/* Edit Modal */}
            {editingAccount && (
                <div className="fixed inset-0 bg-slate-900/60 backdrop-blur-sm z-50 flex items-center justify-center p-4">
                    <div
                        className={`rounded-3xl shadow-2xl w-full max-w-md overflow-hidden animate-in zoom-in-95 duration-200 ${darkMode ? 'bg-slate-800' : 'bg-white'}`}>
                        <div className={`p-6 border-b flex justify-between items-center ${darkMode ? 'bg-slate-900 border-slate-700' : 'bg-slate-50 border-slate-100'}`}>
                            <h3 className={darkMode ? 'text-xl font-bold text-slate-100' : 'text-xl font-bold text-slate-800'}>编辑账号信息</h3>
                            <button onClick={() => setEditingAccount(null)} className={darkMode ? 'text-slate-400 hover:text-slate-200' : 'text-slate-400 hover:text-slate-600'}>
                                <X size={24} />
                            </button>
                        </div>
                        <form onSubmit={handleUpdate} className="p-6 space-y-4">
                            <div className="grid grid-cols-1 gap-4">
                                <div>
                                    <label className={`block text-sm font-medium mb-1 ${darkMode ? 'text-slate-300' : 'text-slate-500'}`}>邮箱账号</label>
                                    <input name="email" defaultValue={editingAccount.email} required
                                        className={`w-full px-4 py-3 border rounded-xl focus:ring-2 focus:ring-blue-500 outline-none transition-all ${darkMode ? 'bg-slate-900 border-slate-600 text-slate-100' : 'bg-slate-50 border-slate-200'}`} />
                                </div>
                                <div>
                                    <label className={`block text-sm font-medium mb-1 ${darkMode ? 'text-slate-300' : 'text-slate-500'}`}>登录密码</label>
                                    <input name="password" defaultValue={editingAccount.password} required
                                        className={`w-full px-4 py-3 border rounded-xl focus:ring-2 focus:ring-blue-500 outline-none transition-all ${darkMode ? 'bg-slate-900 border-slate-600 text-slate-100' : 'bg-slate-50 border-slate-200'}`} />
                                </div>
                                <div>
                                    <label className={`block text-sm font-medium mb-1 ${darkMode ? 'text-slate-300' : 'text-slate-500'}`}>恢复邮箱</label>
                                    <input name="recovery" defaultValue={editingAccount.recovery} required
                                        className={`w-full px-4 py-3 border rounded-xl focus:ring-2 focus:ring-blue-500 outline-none transition-all ${darkMode ? 'bg-slate-900 border-slate-600 text-slate-100' : 'bg-slate-50 border-slate-200'}`} />
                                </div>
                                <div>
                                    <label className={`block text-sm font-medium mb-1 ${darkMode ? 'text-slate-300' : 'text-slate-500'}`}>2FA 密钥</label>
                                    <input name="secret" defaultValue={editingAccount.secret}
                                        className={`w-full px-4 py-3 border rounded-xl focus:ring-2 focus:ring-blue-500 outline-none transition-all ${darkMode ? 'bg-slate-900 border-slate-600 text-slate-100' : 'bg-slate-50 border-slate-200'}`} />
                                </div>
                                <div>
                                    <label className={`block text-sm font-medium mb-1 ${darkMode ? 'text-slate-300' : 'text-slate-500'}`}>备注信息</label>
                                    <input name="remark" defaultValue={editingAccount.remark} placeholder="例如：推特绑定、备用机登录等"
                                        className={`w-full px-4 py-3 border rounded-xl focus:ring-2 focus:ring-blue-500 outline-none transition-all ${darkMode ? 'bg-slate-900 border-slate-600 text-slate-100' : 'bg-slate-50 border-slate-200'}`} />
                                </div>
                            </div>
                            <div className="flex gap-3 pt-4">
                            <button type="button" onClick={() => setEditingAccount(null)} className={`flex-1 py-3 border rounded-xl font-medium transition-all ${darkMode ? 'border-slate-600 text-slate-300 hover:bg-slate-700' : 'border-slate-200 text-slate-600 hover:bg-slate-50'}`}>取消</button>
                                <button type="submit"
                                    className="flex-1 py-3 bg-blue-600 text-white rounded-xl font-medium hover:bg-blue-700 shadow-lg shadow-blue-200 transition-all">确认保存</button>
                            </div>
                        </form>
                    </div>
                </div>
            )}

            {/* Delete Confirmation Modal */}
            {deletingId && (
                <div className="fixed inset-0 bg-slate-900/60 backdrop-blur-sm z-50 flex items-center justify-center p-4">
                    <div
                        className={`rounded-3xl shadow-2xl w-full max-w-sm p-8 text-center animate-in zoom-in-95 duration-200 ${darkMode ? 'bg-slate-800' : 'bg-white'}`}>
                        <div
                            className="w-16 h-16 bg-red-100 text-red-600 rounded-full flex items-center justify-center mx-auto mb-4">
                            <AlertTriangle size={32} />
                        </div>
                        <h3 className={`text-xl font-bold mb-2 ${darkMode ? 'text-slate-100' : 'text-slate-800'}`}>确认删除？</h3>
                        <p className={`mb-8 text-sm ${darkMode ? 'text-slate-400' : 'text-slate-500'}`}>此操作不可撤销，账号信息将从本地库中永久移除。</p>
                        <div className="flex gap-3">
                            <button onClick={() => setDeletingId(null)} className={`flex-1 py-3 rounded-xl font-medium transition-all ${darkMode ? 'bg-slate-700 text-slate-300 hover:bg-slate-600' : 'bg-slate-100 text-slate-600 hover:bg-slate-200'}`}>取消</button>
                            <button onClick={handleDelete}
                                className="flex-1 py-3 bg-red-600 text-white rounded-xl font-medium hover:bg-red-700 shadow-lg shadow-red-200 transition-all">立即删除</button>
                        </div>
                    </div>
                </div>
            )}
        </div>
    );
};

export default App;
