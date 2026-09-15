import React, { useMemo, useState } from 'react';
import { UserPlus, FileText, Plus, Activity } from 'lucide-react';
import { parseAccountImport } from '../utils/accountImport';

/**
 * 导入视图组件
 */
const ImportView = ({ onImport, onCancel, darkMode = false }) => {
    const [importMode, setImportMode] = useState('single'); // 'single' 或 'batch'
    const [text, setText] = useState('');
    const [importing, setImporting] = useState(false);
    const { accounts: preview, errors: parseErrors } = useMemo(() => parseAccountImport(text), [text]);
    const canBatchImport = preview.length > 0 && parseErrors.length === 0 && !importing;

    // 单个账号导入的表单状态
    const [singleForm, setSingleForm] = useState({
        email: '',
        password: '',
        recovery: '',
        secret: '',
    });

    const handleBatchImport = async () => {
        if (!canBatchImport) return;
        setImporting(true);
        try {
            await onImport(preview);
        } finally {
            setImporting(false);
        }
    };

    const handleSingleFormChange = (field, value) => {
        setSingleForm(prev => ({ ...prev, [field]: value }));
    };

    const handleSingleImport = () => {
        if (!singleForm.email.trim() || !singleForm.password.trim()) {
            return;
        }
        onImport([{
            email: singleForm.email.trim(),
            password: singleForm.password.trim(),
            recovery: singleForm.recovery.trim(),
            // 自动去除 2FA 密钥中的空格
            secret: singleForm.secret.trim().replace(/\s/g, ''),
            remark: '',
        }]);
    };

    const isSingleFormValid = singleForm.email.trim() && singleForm.password.trim();
    const panelClass = darkMode ? 'bg-slate-800 border-slate-700' : 'bg-white border-slate-200';
    const inputClass = darkMode
        ? 'bg-slate-900 border-slate-600 text-slate-100 placeholder-slate-500'
        : 'bg-slate-50 border-slate-200 text-slate-800 placeholder-slate-400';
    const secondaryButtonClass = darkMode
        ? 'bg-slate-800 border-slate-600 text-slate-300 hover:bg-slate-700'
        : 'bg-white border-slate-200 text-slate-600 hover:bg-slate-50';

    return (
        <div className="max-w-4xl mx-auto animate-in fade-in zoom-in-95 duration-500">
            <div className="mb-8">
                <h1 className={`text-2xl font-bold ${darkMode ? 'text-slate-100' : 'text-slate-900'}`}>导入账号</h1>
                <p className={darkMode ? 'text-slate-400' : 'text-slate-500'}>导入后状态默认设为"未开启"</p>
            </div>

            {/* 切换标签 */}
            <div className={`flex gap-2 mb-6 p-1 rounded-xl w-fit ${darkMode ? 'bg-slate-800' : 'bg-slate-100'}`}>
                <button
                    onClick={() => setImportMode('single')}
                    disabled={importing}
                    className={`flex items-center gap-2 px-5 py-2.5 rounded-lg font-medium transition-all ${importMode === 'single'
                        ? (darkMode ? 'bg-slate-700 shadow-sm text-blue-400' : 'bg-white shadow-sm text-blue-600')
                        : (darkMode ? 'text-slate-400 hover:text-slate-200' : 'text-slate-500 hover:text-slate-700')
                        }`}
                >
                    <UserPlus size={18} />
                    单个导入
                </button>
                <button
                    onClick={() => setImportMode('batch')}
                    disabled={importing}
                    className={`flex items-center gap-2 px-5 py-2.5 rounded-lg font-medium transition-all ${importMode === 'batch'
                        ? (darkMode ? 'bg-slate-700 shadow-sm text-blue-400' : 'bg-white shadow-sm text-blue-600')
                        : (darkMode ? 'text-slate-400 hover:text-slate-200' : 'text-slate-500 hover:text-slate-700')
                        }`}
                >
                    <FileText size={18} />
                    批量导入
                </button>
            </div>

            {importMode === 'single' ? (
                // 单个导入表单
                <div className="grid grid-cols-1 lg:grid-cols-2 gap-8">
                    <div className="space-y-4">
                        <div className={`p-6 rounded-3xl border shadow-sm ${panelClass}`}>
                            <div className="flex items-center gap-2 mb-6 text-blue-600">
                                <UserPlus size={20} />
                                <h2 className="font-bold">单个账号信息</h2>
                            </div>
                            <div className="space-y-4">
                                <div>
                                    <label className={`block text-sm font-medium mb-2 ${darkMode ? 'text-slate-300' : 'text-slate-600'}`}>
                                        邮箱账号 <span className="text-red-500">*</span>
                                    </label>
                                    <input
                                        type="email"
                                        value={singleForm.email}
                                        onChange={(e) => handleSingleFormChange('email', e.target.value)}
                                        placeholder="example@gmail.com"
                                        className={`w-full px-4 py-3 border rounded-xl focus:ring-2 focus:ring-blue-500 outline-none transition-all ${inputClass}`}
                                    />
                                </div>
                                <div>
                                    <label className={`block text-sm font-medium mb-2 ${darkMode ? 'text-slate-300' : 'text-slate-600'}`}>
                                        登录密码 <span className="text-red-500">*</span>
                                    </label>
                                    <input
                                        type="text"
                                        value={singleForm.password}
                                        onChange={(e) => handleSingleFormChange('password', e.target.value)}
                                        placeholder="输入密码"
                                        className={`w-full px-4 py-3 border rounded-xl focus:ring-2 focus:ring-blue-500 outline-none transition-all ${inputClass}`}
                                    />
                                </div>
                                <div>
                                    <label className={`block text-sm font-medium mb-2 ${darkMode ? 'text-slate-300' : 'text-slate-600'}`}>
                                        恢复邮箱 <span className="text-slate-400 text-xs">(可选)</span>
                                    </label>
                                    <input
                                        type="email"
                                        value={singleForm.recovery}
                                        onChange={(e) => handleSingleFormChange('recovery', e.target.value)}
                                        placeholder="recovery@example.com"
                                        className={`w-full px-4 py-3 border rounded-xl focus:ring-2 focus:ring-blue-500 outline-none transition-all ${inputClass}`}
                                    />
                                </div>
                                <div>
                                    <label className={`block text-sm font-medium mb-2 ${darkMode ? 'text-slate-300' : 'text-slate-600'}`}>
                                        2FA 密钥 <span className="text-slate-400 text-xs">(可选)</span>
                                    </label>
                                    <input
                                        type="text"
                                        value={singleForm.secret}
                                        onChange={(e) => handleSingleFormChange('secret', e.target.value)}
                                        placeholder="TOTP密钥"
                                        className={`w-full px-4 py-3 border rounded-xl focus:ring-2 focus:ring-blue-500 outline-none transition-all ${inputClass}`}
                                    />
                                </div>
                            </div>
                        </div>
                        <div className="flex gap-4">
                            <button onClick={onCancel} className={`flex-1 py-4 border rounded-2xl font-bold transition-all ${secondaryButtonClass}`}>
                                返回列表
                            </button>
                            <button
                                disabled={!isSingleFormValid}
                                onClick={handleSingleImport}
                                className={`flex-1 py-4 px-8 rounded-2xl font-bold flex items-center justify-center gap-2 transition-all ${isSingleFormValid
                                    ? 'bg-blue-600 text-white hover:bg-blue-700 shadow-lg shadow-blue-200'
                                    : 'bg-slate-200 text-slate-400 cursor-not-allowed'
                                    }`}
                            >
                                <Plus size={20} />
                                立即导入
                            </button>
                        </div>
                    </div>

                    <div className="space-y-4">
                        <div className="bg-slate-900 text-white p-6 rounded-3xl shadow-xl h-[516px] flex flex-col">
                            <div className="flex items-center gap-2 mb-6 border-b border-slate-800 pb-4">
                                <div className={`w-2 h-2 rounded-full ${isSingleFormValid ? 'bg-green-500 animate-pulse' : 'bg-slate-600'}`}></div>
                                <h2 className="font-bold">实时预览 (Real-time Preview)</h2>
                            </div>
                            {isSingleFormValid ? (
                                <div className="flex-1 overflow-y-auto space-y-3 pr-2">
                                    <div className="bg-slate-800/50 p-4 rounded-xl border border-slate-700/50">
                                        <div className="flex justify-between mb-3">
                                            <span className="text-blue-400 font-bold text-lg">{singleForm.email}</span>
                                            <span className="text-slate-500">#1</span>
                                        </div>
                                        <div className="grid grid-cols-1 gap-2 text-sm">
                                            <div className="flex justify-between border-b border-slate-800/50 pb-2 pt-1">
                                                <span className="text-slate-500">密码:</span>
                                                <span className="text-slate-300 font-mono">{'•'.repeat(singleForm.password.length)}</span>
                                            </div>
                                            <div className="flex justify-between border-b border-slate-800/50 pb-2 pt-1">
                                                <span className="text-slate-500">恢复邮箱:</span>
                                                <span className={singleForm.recovery ? 'text-slate-300' : 'text-slate-600 italic'}>{singleForm.recovery || '未设置'}</span>
                                            </div>
                                            <div className="flex justify-between border-b border-slate-800/50 pb-2 pt-1">
                                                <span className="text-slate-500">2FA密钥:</span>
                                                <span className={singleForm.secret ? 'text-green-400' : 'text-slate-600 italic'}>{singleForm.secret ? '已设置' : '未设置'}</span>
                                            </div>
                                            <div className="flex justify-between pt-1">
                                                <span className="text-slate-500">状态:</span>
                                                <span className="text-amber-400">未开启 (默认)</span>
                                            </div>
                                        </div>
                                    </div>
                                </div>
                            ) : (
                                <div className="flex-1 flex flex-col items-center justify-center text-slate-600 text-center px-8">
                                    <UserPlus size={48} className="mb-4 opacity-10" />
                                    <p>请填写账号信息</p>
                                    <p className="text-xs mt-2 italic text-slate-500">邮箱和密码为必填项</p>
                                </div>
                            )}
                            <div className="mt-4 pt-4 border-t border-slate-800 text-[10px] text-slate-500 flex justify-between uppercase tracking-widest">
                                <span>Status: {isSingleFormValid ? 'READY' : 'WAITING'}</span>
                                <span>Count: {isSingleFormValid ? 1 : 0}</span>
                            </div>
                        </div>
                    </div>
                </div>
            ) : (
                // 批量导入（原有逻辑）
                <div className="grid grid-cols-1 lg:grid-cols-2 gap-8">
                    <div className="space-y-4">
                        <div className={`p-6 rounded-3xl border shadow-sm ${panelClass}`}>
                            <div className="flex items-center gap-2 mb-4 text-blue-600">
                                <FileText size={20} />
                                <h2 className="font-bold">粘贴数据区域</h2>
                            </div>
                            <p
                                className={`text-xs mb-3 p-3 rounded-lg border border-dashed ${darkMode ? 'text-slate-400 bg-slate-900 border-slate-600' : 'text-slate-400 bg-slate-50 border-slate-200'}`}>
                                格式：邮箱|密码|恢复邮箱|2FA密钥|备注(可选)。支持 |、——、----、--，第五列国家或地区会保存为备注。
                            </p>
                            <textarea
                                className={`w-full h-80 px-4 py-4 border rounded-2xl focus:ring-2 focus:ring-blue-500 outline-none transition-all text-sm resize-none ${inputClass}`}
                                aria-label="批量账号文本"
                                placeholder="example@gmail.com|password123|recovery@example.com|JBSWY3DPEHPK3PXP|UnitedStates"
                                disabled={importing}
                                value={text} onChange={(event) => setText(event.target.value)}
                            />
                            {parseErrors.length > 0 && (
                                <div role="alert" className="mt-3 rounded-xl border border-red-300 p-3 text-sm text-red-500">
                                    <p>检测到 {parseErrors.length} 行格式错误，请修正后再导入：</p>
                                    <ul className="mt-1 space-y-1">
                                        {parseErrors.slice(0, 5).map(error => (
                                            <li key={error.lineNumber}>第 {error.lineNumber} 行：{error.message}</li>
                                        ))}
                                    </ul>
                                    {parseErrors.length > 5 && <p>另有 {parseErrors.length - 5} 行错误。</p>}
                                </div>
                            )}
                        </div>
                        <div className="flex gap-4">
                            <button onClick={onCancel} className={`flex-1 py-4 border rounded-2xl font-bold transition-all ${secondaryButtonClass}`}>
                                返回列表
                            </button>
                            <button
                                disabled={!canBatchImport}
                                onClick={handleBatchImport}
                                className={`flex-2 py-4 px-8 rounded-2xl font-bold flex items-center justify-center gap-2 transition-all ${canBatchImport ? 'bg-blue-600 text-white hover:bg-blue-700 shadow-lg shadow-blue-200' : 'bg-slate-200 text-slate-400 cursor-not-allowed'}`}
                            >
                                <Plus size={20} />
                                {importing ? '正在导入...' : '立即导入'} {preview.length > 0 ? `(${preview.length}条)` : ''}
                            </button>
                        </div>
                    </div>

                    <div className="space-y-4">
                        <div className="bg-slate-900 text-white p-6 rounded-3xl shadow-xl h-[516px] flex flex-col">
                            <div className="flex items-center gap-2 mb-6 border-b border-slate-800 pb-4">
                                <div className="w-2 h-2 rounded-full bg-green-500 animate-pulse"></div>
                                <h2 className="font-bold">实时预览 (Real-time Preview)</h2>
                            </div>
                            {preview.length > 0 ? (
                                <div className="flex-1 overflow-y-auto space-y-3 pr-2 scrollbar-thin scrollbar-thumb-slate-700">
                                    {preview.map((item, idx) => (
                                        <div key={idx} className="bg-slate-800/50 p-3 rounded-xl border border-slate-700/50 text-xs">
                                            <div className="flex justify-between mb-1">
                                                <span className="text-blue-400 font-bold">{item.email || '未知账号'}</span>
                                                <span className="text-slate-500">#{idx + 1}</span>
                                            </div>
                                            <div className="grid grid-cols-1 gap-1 text-slate-400 mt-2">
                                                <div className="flex justify-between border-b border-slate-800/50 pb-1 pt-1">
                                                    <span className="text-slate-500">状态:</span> <span className="text-slate-400">未开启 (默认)</span>
                                                </div>
                                                <div className="flex justify-between border-b border-slate-800/50 pb-1 pt-1">
                                                    <span className="text-slate-500">备注:</span> <span className="text-green-400">{item.remark || '无'}</span>
                                                </div>
                                            </div>
                                        </div>
                                    ))}
                                </div>
                            ) : (
                                <div className="flex-1 flex flex-col items-center justify-center text-slate-600 text-center px-8">
                                    <Activity size={48} className="mb-4 opacity-10" />
                                    <p>等待解析数据...</p>
                                    <p className="text-xs mt-2 italic text-slate-500">所有导入项初始状态均为"未开启"</p>
                                </div>
                            )}
                            <div className="mt-4 pt-4 border-t border-slate-800 text-[10px] text-slate-500 flex justify-between uppercase tracking-widest">
                                <span>Status: {canBatchImport ? 'READY' : importing ? 'IMPORTING' : parseErrors.length > 0 ? 'INVALID' : 'WAITING'}</span>
                                <span>Count: {preview.length}</span>
                            </div>
                        </div>
                    </div>
                </div>
            )}
        </div>
    );
};

export default ImportView;
