import React, { useState, useEffect, useMemo } from 'react';
import {
    Users,
    UserPlus,
    ShieldCheck,
    X,
    CheckCircle2,
    AlertTriangle,
    Moon,
    Sun,
    LogOut,
    MailCheck
} from 'lucide-react';

// 导入服务和组件
import api from './services/api';
import AccountListView from './components/AccountListView';
import ImportView from './components/ImportView';
import LoginPage from './components/LoginPage';
import GooglemailView from './components/GooglemailView';

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

    // 登录状态 - 使用 localStorage 并检查7天有效期
    const [isLoggedIn, setIsLoggedIn] = useState(() => {
        const loginData = localStorage.getItem('loginData');
        if (!loginData) return false;

        try {
            const { timestamp } = JSON.parse(loginData);
            const sevenDaysMs = 7 * 24 * 60 * 60 * 1000; // 7天毫秒数
            const isValid = Date.now() - timestamp < sevenDaysMs;

            if (!isValid) {
                // 已过期，清除登录状态
                localStorage.removeItem('loginData');
                return false;
            }
            return true;
        } catch {
            return false;
        }
    });

    // 暗色模式状态
    const [darkMode, setDarkMode] = useState(() => {
        // 从 localStorage 读取用户偏好
        const saved = localStorage.getItem('darkMode');
        return saved ? JSON.parse(saved) : false;
    });

    // 保存暗色模式设置到 localStorage
    useEffect(() => {
        localStorage.setItem('darkMode', JSON.stringify(darkMode));
    }, [darkMode]);

    // --- 加载账号数据 ---
    useEffect(() => {
        if (isLoggedIn) {
            loadAccounts();
        } else {
            setAccounts([]);
            setLoading(false);
        }
    }, [isLoggedIn]);

    const loadAccounts = async () => {
        try {
            setLoading(true);
            const data = await api.getAccounts();
            setAccounts(data);
        } catch (error) {
            console.error('加载账号失败:', error);
            if (error.status === 401) {
                localStorage.removeItem('loginData');
                setIsLoggedIn(false);
                showNotification('登录状态已失效，请重新登录', 'error');
                return;
            }
            showNotification('加载账号失败', 'error');
        } finally {
            setLoading(false);
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
        setNotification({ msg, type });
        setTimeout(() => setNotification(null), 3000);
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
                setAccounts(accounts.map(acc =>
                    acc.id === id ? result.data : acc
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
        // 如果当前是已售出状态，点击后要切换为未售出，需要二次确认
        if (currentStatus === 'sold') {
            const confirmed = window.confirm('确定要将该账号标记为"未售出"吗？\n\n这将撤销之前的售出记录。');
            if (!confirmed) {
                return;
            }
        }

        try {
            const result = await api.toggleSoldStatus(id);
            if (result.success) {
                setAccounts(accounts.map(acc =>
                    acc.id === id ? result.data : acc
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
                setAccounts(accounts.filter(acc => acc.id !== deletingId));
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
                setAccounts(accounts.map(acc =>
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
            await api.logout();
        } catch (error) {
            console.error('退出登录失败:', error);
        } finally {
            localStorage.removeItem('loginData');
            setIsLoggedIn(false);
            setView('list');
        }
    };

    const handleImport = async (importedList) => {
        try {
            const result = await api.batchImport(importedList);
            if (result.success) {
                // 重新加载账号列表
                await loadAccounts();
                setView('list');

                // 显示导入结果
                const { success_count, failed_count, failed_emails } = result.data;
                if (failed_count > 0) {
                    // 有重复账号
                    const duplicateInfo = failed_emails.slice(0, 3).join('、');
                    const moreInfo = failed_emails.length > 3 ? `等${failed_emails.length}个` : '';
                    showNotification(
                        `成功导入 ${success_count} 个账号，${failed_count} 个账号已存在：${duplicateInfo}${moreInfo}`,
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
            showNotification('导入失败', 'error');
        }
    };

    const filteredAccounts = useMemo(() => {
        return accounts.filter(acc =>
            acc.email.toLowerCase().includes(search.toLowerCase()) ||
            (acc.remark && acc.remark.toLowerCase().includes(search.toLowerCase()))
        );
    }, [accounts, search]);

    const globalFontStyle = {
        fontFamily: '"Times New Roman", Times, serif',
    };

    // 未登录时显示登录页面
    if (!isLoggedIn) {
        return <LoginPage onLoginSuccess={() => setIsLoggedIn(true)} darkMode={darkMode} />;
    }

    return (
        <div className={`min-h-screen transition-colors duration-300 ${darkMode ? 'bg-slate-900 text-slate-100' : 'bg-slate-50 text-slate-800'}`} style={globalFontStyle}>
            <style>
                {
                    ` * {
                    font-family: "Times New Roman", Times, serif !important;
                }

                .font-mono {
                    font-family: ui-monospace, monospace !important;
                }

                `
                }
            </style>

            <nav className={`${darkMode ? 'bg-slate-800 border-slate-700' : 'bg-white border-slate-200'} border-b sticky top-0 z-30 transition-colors duration-300`}>
                <div className="max-w-[1600px] mx-auto px-4 sm:px-6 lg:px-8">
                    <div className="flex justify-between h-16 items-center">
                        <div className="flex items-center gap-2 flex-shrink-0">
                            <div className="bg-blue-600 p-2 rounded-lg">
                                <ShieldCheck className="text-white w-6 h-6" />
                            </div>
                            <span
                                className={`hidden sm:inline-block text-xl font-bold bg-clip-text text-transparent bg-gradient-to-r ${darkMode ? 'from-blue-400 to-indigo-400' : 'from-blue-600 to-indigo-600'}`}>
                                GoogleManager
                            </span>
                        </div>

                        <div className="flex items-center gap-1 sm:gap-4 min-w-0">

                            <div className={`flex gap-1 ${darkMode ? 'bg-slate-700' : 'bg-slate-100'} p-1 rounded-xl`}>
                                <button onClick={() => setView('list')} title="账号列表" aria-label="账号列表"
                                    className={`flex items-center gap-2 px-4 py-2 rounded-lg transition-all ${view === 'list' ?
                                        (darkMode ? 'bg-slate-600 shadow-sm text-blue-400' : 'bg-white shadow-sm text-blue-600')
                                        : (darkMode ? 'text-slate-400 hover:text-slate-200' : 'text-slate-500 hover:text-slate-700')}`}
                                >
                                    <Users size={18} />
                                    <span className="font-medium hidden sm:inline">账号列表</span>
                                </button>
                                <button onClick={() => setView('import')} title="导入账号" aria-label="导入账号"
                                    className={`flex items-center gap-2 px-4 py-2 rounded-lg transition-all ${view === 'import' ?
                                        (darkMode ? 'bg-slate-600 shadow-sm text-blue-400' : 'bg-white shadow-sm text-blue-600')
                                        : (darkMode ? 'text-slate-400 hover:text-slate-200' : 'text-slate-500 hover:text-slate-700')}`}
                                >
                                    <UserPlus size={18} />
                                    <span className="font-medium hidden sm:inline">导入账号</span>
                                </button>
                                <button onClick={() => setView('googlemail')} title="Googlemail" aria-label="Googlemail"
                                    className={`flex items-center gap-2 px-4 py-2 rounded-lg transition-all ${view === 'googlemail' ?
                                        (darkMode ? 'bg-slate-600 shadow-sm text-cyan-400' : 'bg-white shadow-sm text-cyan-600')
                                        : (darkMode ? 'text-slate-400 hover:text-slate-200' : 'text-slate-500 hover:text-slate-700')}`}
                                >
                                    <MailCheck size={18} />
                                    <span className="font-medium hidden lg:inline">Googlemail</span>
                                </button>
                            </div>

                            {/* 暗色模式切换按钮 */}
                            <button
                                onClick={() => setDarkMode(!darkMode)}
                                className={`p-2.5 rounded-xl transition-all ${darkMode
                                    ? 'bg-slate-700 text-yellow-400 hover:bg-slate-600'
                                    : 'bg-slate-100 text-slate-600 hover:bg-slate-200'}`}
                                title={darkMode ? '切换亮色模式' : '切换暗色模式'}
                            >
                                {darkMode ? <Sun size={20} /> : <Moon size={20} />}
                            </button>

                            <button
                                onClick={handleLogout}
                                className={`p-2.5 rounded-xl transition-all ${darkMode
                                    ? 'bg-slate-700 text-slate-300 hover:bg-slate-600'
                                    : 'bg-slate-100 text-slate-600 hover:bg-slate-200'}`}
                                title="退出登录"
                            >
                                <LogOut size={20} />
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
                        loading={loading}
                        darkMode={darkMode}
                    />
                ) : view === 'import' ? (
                    <ImportView onImport={handleImport} onCancel={() => setView('list')} darkMode={darkMode} />
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
                <div className={`fixed bottom-8 right-8 flex items-center gap-3 px-6 py-4 rounded-2xl shadow-2xl transition-all
            animate-bounce z-[100] ${notification.type === 'success' ? 'bg-green-600 text-white' : 'bg-red-600 text-white'
                    }`}>
                    <CheckCircle2 size={20} />
                    <span className="font-medium">{notification.msg}</span>
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
