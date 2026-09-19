import React, { useState, useEffect } from 'react';
import { X, Clock, Key, Mail, Shield, History, ArrowRight, ChevronDown, ChevronUp, AlertTriangle, MailCheck } from 'lucide-react';
import api from '../services/api';

/**
 * 历史记录抽屉组件
 * 显示账号的修改历史
 */
const HistoryDrawer = ({ isOpen, onClose, account, darkMode }) => {
    const [history, setHistory] = useState([]);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState('');
    const [expandedFields, setExpandedFields] = useState({});

    // 字段名称映射
    const fieldNames = {
        password: { name: '密码', icon: Key, color: 'green', gradient: 'from-green-500 to-emerald-600' },
        secret: { name: '2FA密钥', icon: Shield, color: 'blue', gradient: 'from-blue-500 to-indigo-600' },
        recovery: { name: '恢复邮箱', icon: Mail, color: 'orange', gradient: 'from-orange-500 to-amber-600' },
        sold_status: { name: '售出状态', icon: History, color: 'purple', gradient: 'from-purple-500 to-pink-600' },
        status: { name: '账号状态', icon: Shield, color: 'blue', gradient: 'from-blue-600 to-indigo-600' },
        security_action: { name: '安全应急处置', icon: AlertTriangle, color: 'red', gradient: 'from-red-500 to-rose-600' },
        gmail_oauth: { name: 'Gmail授权', icon: MailCheck, color: 'emerald', gradient: 'from-teal-500 to-emerald-600' },
    };

    // 售出状态值映射
    const soldStatusLabels = {
        'sold': '已售出',
        'unsold': '未售出'
    };

    // 账号状态值映射
    const statusLabels = {
        'inactive': '未开启 (默认)',
        'pro': 'Pro 专业版',
        'locked': '已应急锁定保护',
    };

    // 加载历史记录
    useEffect(() => {
        if (!isOpen || !account) return undefined;
        let active = true;
        setLoading(true);
        setHistory([]);
        setError('');
        setExpandedFields({
            password: true,
            secret: true,
            recovery: true,
            sold_status: true,
            status: true,
            security_action: true,
            gmail_oauth: true,
        });
        const loadHistory = async () => {
            try {
                const result = await api.getAccountHistory(account.id);
                if (!active) return;
                if (result.success) {
                    setHistory(result.data);
                } else {
                    setError(result.message || '加载历史记录失败');
                }
            } catch (requestError) {
                if (active) setError('加载历史记录失败，请重新打开重试');
            } finally {
                if (active) setLoading(false);
            }
        };
        loadHistory();
        return () => { active = false; };
    }, [isOpen, account?.id]);

    // 切换字段展开状态 (默认展开)
    const toggleField = (field) => {
        setExpandedFields(prev => ({
            ...prev,
            [field]: prev[field] !== undefined ? !prev[field] : false
        }));
    };

    // 按字段分组历史记录
    const groupedHistory = history.reduce((acc, item) => {
        if (!acc[item.fieldName]) {
            acc[item.fieldName] = [];
        }
        acc[item.fieldName].push(item);
        return acc;
    }, {});

    // 获取所有待显示的字段键
    const allFieldKeys = Array.from(new Set([...Object.keys(fieldNames), ...Object.keys(groupedHistory)]));

    // 格式化时间显示
    const formatTime = (timeStr) => {
        if (!timeStr) return '';
        const parts = timeStr.split(' ');
        return {
            date: parts[0] || '',
            time: parts[1] || ''
        };
    };

    if (!isOpen) return null;

    return (
        <>
            {/* 遮罩层 */}
            <div
                className="fixed inset-0 bg-black/60 backdrop-blur-sm z-40 transition-opacity"
                onClick={onClose}
            />

            {/* 抽屉面板 */}
            <div className={`fixed right-0 top-0 h-full w-full max-w-[420px] z-50 transform transition-all duration-300 ease-out shadow-2xl ${darkMode
                ? 'bg-gradient-to-b from-slate-800 to-slate-900'
                : 'bg-gradient-to-b from-white to-slate-50'
                }`}>
                {/* 头部 - 渐变背景 */}
                <div className="bg-gradient-to-r from-purple-600 to-indigo-600 p-5 text-white">
                    <div className="flex items-center justify-between">
                        <div className="flex items-center gap-3">
                            <div className="p-2 bg-white/20 rounded-xl">
                                <History size={22} />
                            </div>
                            <div>
                                <h2 className="font-bold text-lg">修改历史</h2>
                                <p className="text-white/70 text-sm">查看账号信息变更记录</p>
                            </div>
                        </div>
                        <button
                            onClick={onClose}
                            aria-label="关闭修改历史"
                            className="p-2 hover:bg-white/20 rounded-xl transition-colors"
                        >
                            <X size={20} />
                        </button>
                    </div>
                </div>

                {/* 账号信息卡片 */}
                {account && (
                    <div className={`mx-4 -mt-4 p-4 rounded-2xl shadow-lg ${darkMode ? 'bg-slate-700' : 'bg-white'}`}>
                        <p className={`text-xs font-medium mb-1 ${darkMode ? 'text-slate-400' : 'text-slate-500'}`}>当前账号</p>
                        <p className={`font-bold truncate ${darkMode ? 'text-slate-100' : 'text-slate-800'}`}>{account.email}</p>
                    </div>
                )}

                {/* 历史记录内容 */}
                <div className="p-4 overflow-y-auto h-[calc(100%-200px)]">
                    {loading ? (
                        <div className="flex items-center justify-center py-20">
                            <div className="relative">
                                <div className="animate-spin rounded-full h-12 w-12 border-4 border-purple-200 border-t-purple-600"></div>
                                <History className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 text-purple-600" size={20} />
                            </div>
                        </div>
                    ) : error ? (
                        <p role="alert" className="py-16 text-center text-red-500">{error}</p>
                    ) : history.length === 0 ? (
                        <div className={`text-center py-16 ${darkMode ? 'text-slate-500' : 'text-slate-400'}`}>
                            <div className={`w-20 h-20 mx-auto mb-4 rounded-full flex items-center justify-center ${darkMode ? 'bg-slate-700' : 'bg-slate-100'}`}>
                                <History size={32} className="opacity-40" />
                            </div>
                            <p className="font-medium mb-1">暂无修改记录</p>
                            <p className="text-sm">修改密码、2FA密钥或恢复邮箱后<br />会在这里显示历史记录</p>
                        </div>
                    ) : (
                        <div className="space-y-4">
                            {allFieldKeys.map((fieldKey) => {
                                const records = groupedHistory[fieldKey] || [];
                                if (records.length === 0) return null;

                                const fieldInfo = fieldNames[fieldKey] || {
                                    name: fieldKey,
                                    icon: History,
                                    color: 'slate',
                                    gradient: 'from-slate-500 to-slate-600'
                                };
                                const FieldIcon = fieldInfo.icon;
                                const isExpanded = expandedFields[fieldKey] !== false;

                                return (
                                    <div key={fieldKey} className={`rounded-2xl overflow-hidden shadow-md ${darkMode ? 'bg-slate-700/50' : 'bg-white'}`}>
                                        {/* 字段标题 - 可点击折叠 */}
                                        <button
                                            onClick={() => toggleField(fieldKey)}
                                            className={`w-full flex items-center justify-between p-4 transition-colors ${darkMode ? 'hover:bg-slate-600/50' : 'hover:bg-slate-50'}`}
                                        >
                                            <div className="flex items-center gap-3">
                                                <div className={`p-2 rounded-xl bg-gradient-to-br ${fieldInfo.gradient} text-white shadow-lg`}>
                                                    <FieldIcon size={16} />
                                                </div>
                                                <span className={`font-bold ${darkMode ? 'text-slate-200' : 'text-slate-700'}`}>
                                                    {fieldInfo.name}修改记录
                                                </span>
                                            </div>
                                            <div className="flex items-center gap-2">
                                                <span className={`text-xs px-2.5 py-1 rounded-full font-bold ${darkMode ? 'bg-purple-900/50 text-purple-300' : 'bg-purple-100 text-purple-600'}`}>
                                                    {records.length}次
                                                </span>
                                                {isExpanded ? <ChevronUp size={18} className="text-slate-400" /> : <ChevronDown size={18} className="text-slate-400" />}
                                            </div>
                                        </button>

                                        {/* 记录列表 */}
                                        {isExpanded && (
                                            <div className={`px-4 pb-4 space-y-3 ${darkMode ? 'border-t border-slate-600' : 'border-t border-slate-100'}`}>
                                                {records.map((record, idx) => {
                                                    const { date, time } = formatTime(record.changedAt);
                                                    // 格式化显示值（售出状态与账号状态转换为中文）
                                                    const formatValue = (val) => {
                                                        if (fieldKey === 'sold_status') {
                                                            return soldStatusLabels[val] || val;
                                                        }
                                                        if (fieldKey === 'status') {
                                                            return statusLabels[val] || val;
                                                        }
                                                        return val;
                                                    };
                                                    return (
                                                        <div
                                                            key={record.id}
                                                            className={`mt-3 p-4 rounded-xl ${darkMode ? 'bg-slate-800/70' : 'bg-slate-50'}`}
                                                        >
                                                            {/* 时间标签 */}
                                                            <div className={`flex items-center gap-2 text-xs mb-3 ${darkMode ? 'text-slate-400' : 'text-slate-500'}`}>
                                                                <Clock size={12} />
                                                                <span className="font-medium">{date}</span>
                                                                <span className={`px-1.5 py-0.5 rounded ${darkMode ? 'bg-slate-700' : 'bg-slate-200'}`}>{time}</span>
                                                            </div>

                                                            {/* 修改内容 - 优化显示 */}
                                                            <div className="flex items-center gap-3">
                                                                {/* 旧值 */}
                                                                <div className={`flex-1 p-3 rounded-xl ${darkMode ? 'bg-red-900/20 border border-red-800/30' : 'bg-red-50 border border-red-100'}`}>
                                                                    <p className={`text-[10px] font-medium mb-1 ${darkMode ? 'text-red-400' : 'text-red-500'}`}>修改前</p>
                                                                    <p className={`text-sm font-mono break-all ${darkMode ? 'text-red-300' : 'text-red-600'}`}>
                                                                        {formatValue(record.oldValue) || <span className="italic opacity-50">(空)</span>}
                                                                    </p>
                                                                </div>

                                                                {/* 箭头 */}
                                                                <div className={`p-2 rounded-full ${darkMode ? 'bg-slate-700' : 'bg-white shadow'}`}>
                                                                    <ArrowRight size={14} className={darkMode ? 'text-slate-400' : 'text-slate-500'} />
                                                                </div>

                                                                {/* 新值 */}
                                                                <div className={`flex-1 p-3 rounded-xl ${darkMode ? 'bg-green-900/20 border border-green-800/30' : 'bg-green-50 border border-green-100'}`}>
                                                                    <p className={`text-[10px] font-medium mb-1 ${darkMode ? 'text-green-400' : 'text-green-500'}`}>修改后</p>
                                                                    <p className={`text-sm font-mono break-all ${darkMode ? 'text-green-300' : 'text-green-600'}`}>
                                                                        {formatValue(record.newValue) || <span className="italic opacity-50">(空)</span>}
                                                                    </p>
                                                                </div>
                                                            </div>
                                                        </div>
                                                    );
                                                })}
                                            </div>
                                        )}
                                    </div>
                                );
                            })}
                        </div>
                    )}
                </div>

                {/* 底部信息 */}
                <div className={`absolute bottom-0 left-0 right-0 p-4 ${darkMode ? 'bg-slate-800/80 border-t border-slate-700' : 'bg-white/80 border-t border-slate-100'} backdrop-blur-sm`}>
                    <p className={`text-xs text-center ${darkMode ? 'text-slate-500' : 'text-slate-400'}`}>
                        共 {history.length} 条修改记录
                    </p>
                </div>
            </div>
        </>
    );
};

export default HistoryDrawer;
