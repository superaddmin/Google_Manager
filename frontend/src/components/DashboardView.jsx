import React, { useEffect, useState } from 'react';
import {
    AlertTriangle,
    BarChart3,
    KeyRound,
    Loader2,
    MailPlus,
    RefreshCw,
    TrendingUp,
    Users,
} from 'lucide-react';
import api from '../services/api';

const formatDateTime = (isoString) => {
    try {
        return new Date(isoString).toLocaleString('zh-CN', { hour12: false });
    } catch {
        return isoString;
    }
};

const DashboardView = ({ darkMode }) => {
    const [stats, setStats] = useState(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');

    const loadStats = async () => {
        try {
            setError('');
            setLoading(true);
            const result = await api.getStats();
            if (result.success) {
                setStats(result.data);
            } else {
                setError(result.message || '加载统计信息失败');
            }
        } catch (requestError) {
            console.error('加载统计信息失败:', requestError);
            setError(requestError.message || '加载统计信息失败');
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => {
        loadStats();
    }, []);

    const panelClass = darkMode
        ? 'bg-slate-800 border-slate-700'
        : 'bg-white border-slate-200';

    if (loading) {
        return (
            <div className="min-h-[420px] flex items-center justify-center">
                <Loader2 className="animate-spin text-blue-500" size={34} />
            </div>
        );
    }

    const percent = (value, total) => (total > 0 ? Math.round((value / total) * 100) : 0);
    const trendMax = stats
        ? Math.max(1, ...stats.recentImports.map(item => item.count),
            ...stats.recentSales.map(item => item.count))
        : 1;

    const statCards = stats ? [
        {
            label: '账号总数',
            value: stats.total,
            sub: `Pro ${stats.pro} / 标准 ${stats.standard}`,
            icon: Users,
            color: 'bg-blue-600',
        },
        {
            label: '未售出',
            value: stats.unsold,
            sub: `在库 ${percent(stats.unsold, stats.total)}%`,
            icon: TrendingUp,
            color: 'bg-green-600',
        },
        {
            label: '已售出',
            value: stats.sold,
            sub: `售出率 ${percent(stats.sold, stats.total)}%`,
            icon: BarChart3,
            color: 'bg-red-500',
        },
        {
            label: '2FA 覆盖',
            value: stats.with2fa,
            sub: `覆盖率 ${percent(stats.with2fa, stats.total)}% · 缺失 ${stats.without2fa}`,
            icon: KeyRound,
            color: 'bg-purple-600',
        },
        {
            label: '恢复邮箱覆盖',
            value: stats.withRecovery,
            sub: `覆盖率 ${percent(stats.withRecovery, stats.total)}% · 缺失 ${stats.withoutRecovery}`,
            icon: MailPlus,
            color: 'bg-cyan-600',
        },
    ] : [];

    const renderTrendBars = (series) => (
        <div className="flex items-end gap-1.5 h-32 mt-4">
            {series.map(item => (
                <div key={item.date} className="flex-1 flex flex-col items-center gap-1 group relative">
                    <span className={`text-[10px] font-bold opacity-0 group-hover:opacity-100 transition-opacity ${darkMode ? 'text-slate-300' : 'text-slate-600'}`}>
                        {item.count}
                    </span>
                    <div
                        className="w-full rounded-t-md bg-blue-500/80 transition-all group-hover:bg-blue-500 min-h-[2px]"
                        style={{ height: `${Math.max((item.count / trendMax) * 100, 1.5)}%` }}
                        title={`${item.date}：${item.count}`}
                    />
                    <span className={`text-[9px] ${darkMode ? 'text-slate-500' : 'text-slate-400'}`}>
                        {item.date.slice(5)}
                    </span>
                </div>
            ))}
        </div>
    );

    return (
        <div className="space-y-6 animate-in fade-in slide-in-from-bottom-4 duration-500">
            <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
                <div className="flex items-center gap-3">
                    <div className="p-2.5 bg-indigo-600 text-white rounded-lg">
                        <BarChart3 size={22} />
                    </div>
                    <div>
                        <h1 className={`text-2xl font-bold ${darkMode ? 'text-slate-100' : 'text-slate-900'}`}>统计看板</h1>
                        <p className={`text-sm ${darkMode ? 'text-slate-400' : 'text-slate-500'}`}>账号资产总览与近期趋势（近 14 天）</p>
                    </div>
                </div>
                <button
                    onClick={loadStats}
                    className={`p-2.5 rounded-lg border transition-colors ${panelClass}`}
                    title="刷新统计"
                >
                    <RefreshCw size={18} />
                </button>
            </div>

            {error && (
                <div className="flex items-center gap-3 px-4 py-3 bg-red-50 border border-red-200 text-red-700 rounded-lg">
                    <AlertTriangle size={18} />
                    <span className="text-sm">{error}</span>
                </div>
            )}

            {stats && (
                <>
                    <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-5 gap-4">
                        {statCards.map(card => (
                            <div key={card.label} className={`rounded-2xl border p-5 shadow-sm ${panelClass}`}>
                                <div className="flex items-center justify-between">
                                    <span className={`text-sm font-medium ${darkMode ? 'text-slate-400' : 'text-slate-500'}`}>
                                        {card.label}
                                    </span>
                                    <div className={`${card.color} p-2 rounded-lg`}>
                                        <card.icon size={16} className="text-white" />
                                    </div>
                                </div>
                                <p className={`text-3xl font-bold mt-3 ${darkMode ? 'text-slate-100' : 'text-slate-900'}`}>
                                    {card.value}
                                </p>
                                <p className={`text-xs mt-1 ${darkMode ? 'text-slate-500' : 'text-slate-400'}`}>
                                    {card.sub}
                                </p>
                            </div>
                        ))}
                    </div>

                    <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
                        <section className={`rounded-2xl border p-5 shadow-sm ${panelClass}`}>
                            <h2 className={`font-bold ${darkMode ? 'text-slate-100' : 'text-slate-800'}`}>导入趋势（近 14 天）</h2>
                            {renderTrendBars(stats.recentImports)}
                        </section>
                        <section className={`rounded-2xl border p-5 shadow-sm ${panelClass}`}>
                            <h2 className={`font-bold ${darkMode ? 'text-slate-100' : 'text-slate-800'}`}>售出趋势（近 14 天）</h2>
                            {renderTrendBars(stats.recentSales)}
                        </section>
                    </div>

                    <p className={`text-xs ${darkMode ? 'text-slate-500' : 'text-slate-400'}`}>
                        统计生成时间：{formatDateTime(new Date().toISOString())}
                    </p>
                </>
            )}
        </div>
    );
};

export default DashboardView;
