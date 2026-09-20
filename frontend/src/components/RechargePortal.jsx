import React, { useEffect, useRef, useState } from 'react';
import { CheckCircle2, Moon, ShieldCheck, Sun } from 'lucide-react';
import RechargeView from './RechargeView';

const RechargePortal = () => {
    const [darkMode, setDarkMode] = useState(() => {
        try {
            return JSON.parse(localStorage.getItem('darkMode')) === true;
        } catch {
            return false;
        }
    });
    const [notification, setNotification] = useState(null);
    const notificationTimerRef = useRef(null);

    useEffect(() => {
        try {
            localStorage.setItem('darkMode', JSON.stringify(darkMode));
        } catch {}
        document.documentElement.classList.toggle('dark', darkMode);
        document.title = 'GoogleManager 充值中心';
    }, [darkMode]);

    useEffect(() => () => clearTimeout(notificationTimerRef.current), []);

    const showNotification = (message, type = 'success') => {
        clearTimeout(notificationTimerRef.current);
        setNotification({ message, type });
        notificationTimerRef.current = setTimeout(() => setNotification(null), 3000);
    };

    return (
        <div className={`min-h-screen transition-colors duration-300 ${darkMode ? 'bg-slate-950 text-slate-100' : 'bg-slate-50 text-slate-800'}`}>
            <header className={`border-b ${darkMode ? 'bg-slate-900 border-slate-800' : 'bg-white border-slate-200'}`}>
                <div className="max-w-7xl mx-auto h-16 px-4 sm:px-6 flex items-center justify-between">
                    <div className="flex items-center gap-3">
                        <div className="p-2.5 rounded-lg bg-emerald-600 text-white">
                            <ShieldCheck size={20} />
                        </div>
                        <div>
                            <p className="font-bold leading-tight">GoogleManager 充值中心</p>
                            <p className={`text-xs ${darkMode ? 'text-slate-400' : 'text-slate-500'}`}>自助充值、进度查询与订阅管理</p>
                        </div>
                    </div>
                    <button
                        type="button"
                        onClick={() => setDarkMode(current => !current)}
                        className={`w-10 h-10 inline-flex items-center justify-center rounded-lg border transition-colors ${darkMode ? 'border-slate-700 text-amber-400 hover:bg-slate-800' : 'border-slate-200 text-slate-600 hover:bg-slate-100'}`}
                        title={darkMode ? '切换亮色模式' : '切换暗色模式'}
                        aria-label={darkMode ? '切换亮色模式' : '切换暗色模式'}
                    >
                        {darkMode ? <Sun size={18} /> : <Moon size={18} />}
                    </button>
                </div>
            </header>

            <main className="max-w-7xl mx-auto px-4 sm:px-6 py-6 sm:py-8">
                <RechargeView darkMode={darkMode} showNotification={showNotification} />
            </main>

            {notification && (
                <div className={`fixed bottom-6 right-6 max-w-sm flex items-center gap-3 px-5 py-3.5 rounded-lg shadow-xl z-[100] border ${
                    notification.type === 'success'
                        ? 'bg-emerald-600 text-white border-emerald-500'
                        : 'bg-red-600 text-white border-red-500'
                }`}>
                    <CheckCircle2 size={19} />
                    <span className="font-medium text-sm">{notification.message}</span>
                </div>
            )}
        </div>
    );
};

export default RechargePortal;
