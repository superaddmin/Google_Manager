import React, { useState, useEffect, useRef } from 'react';
import {
    CreditCard,
    CheckCircle2,
    AlertTriangle,
    Shield,
    FileText,
    Search,
    RefreshCw,
    Clock,
    Zap,
    ExternalLink,
    Copy,
    Check,
    X,
    AlertCircle,
    Download,
    Power,
    HelpCircle,
    Layers,
    ListFilter,
    Eye,
    EyeOff
} from 'lucide-react';
import api from '../services/api';

const RECHARGE_POLL_MAX_DURATION_MS = 30 * 60 * 1000;
// Keep client-side validation aligned with the backend's Unicode \w-based rule.
const EMAIL_PATTERN = /^[\p{L}\p{N}_.-]+@[\p{L}\p{N}_.-]+\.[\p{L}\p{N}_]+$/u;
const AGREEMENT_ALLOWED_TAGS = new Set([
    'a', 'article', 'b', 'blockquote', 'br', 'code', 'del', 'div', 'em', 'h1', 'h2', 'h3',
    'h4', 'h5', 'h6', 'hr', 'i', 'li', 'ol', 'p', 'pre', 'section', 'span', 'strong',
    'table', 'tbody', 'td', 'th', 'thead', 'tr', 'u', 'ul'
]);
const AGREEMENT_DROPPED_TAGS = new Set([
    'audio', 'base', 'button', 'canvas', 'embed', 'form', 'iframe', 'input', 'link', 'math',
    'meta', 'object', 'option', 'script', 'select', 'style', 'svg', 'template', 'textarea', 'video'
]);
const AGREEMENT_ALLOWED_CLASSES = new Set([
    'agreement-content', 'dark:text-emerald-400', 'font-bold', 'space-y-3', 'text-base',
    'text-emerald-600', 'text-sm'
]);
const AGREEMENT_VOID_TAGS = new Set(['br', 'hr']);

const getSafeAgreementHref = (rawHref) => {
    const href = String(rawHref || '').trim();
    if (!href) return '';
    try {
        const parsed = new URL(href, window.location.origin);
        if (!['http:', 'https:', 'mailto:'].includes(parsed.protocol)) return '';
        return href;
    } catch {
        return '';
    }
};

const renderAgreementNode = (node, key) => {
    if (node.nodeType === 3) return node.textContent;
    if (node.nodeType !== 1) return null;

    const tagName = node.tagName.toLowerCase();
    if (AGREEMENT_DROPPED_TAGS.has(tagName)) return null;
    const children = Array.from(node.childNodes).map((child, index) => (
        renderAgreementNode(child, `${key}-${index}`)
    ));
    if (!AGREEMENT_ALLOWED_TAGS.has(tagName)) {
        return <React.Fragment key={key}>{children}</React.Fragment>;
    }

    const props = { key };
    const safeClasses = String(node.getAttribute('class') || '')
        .split(/\s+/)
        .filter(className => AGREEMENT_ALLOWED_CLASSES.has(className));
    if (safeClasses.length > 0) props.className = safeClasses.join(' ');
    if (tagName === 'a') {
        const href = getSafeAgreementHref(node.getAttribute('href'));
        if (!href) return <span key={key}>{children}</span>;
        props.href = href;
        props.rel = 'noopener noreferrer';
        if (node.getAttribute('target') === '_blank') props.target = '_blank';
        const title = node.getAttribute('title');
        if (title) props.title = title;
    }
    if (['td', 'th'].includes(tagName)) {
        const colSpan = Number.parseInt(node.getAttribute('colspan'), 10);
        const rowSpan = Number.parseInt(node.getAttribute('rowspan'), 10);
        if (Number.isInteger(colSpan) && colSpan > 0 && colSpan <= 100) props.colSpan = colSpan;
        if (Number.isInteger(rowSpan) && rowSpan > 0 && rowSpan <= 100) props.rowSpan = rowSpan;
    }
    if (AGREEMENT_VOID_TAGS.has(tagName)) return React.createElement(tagName, props);
    return React.createElement(tagName, props, children);
};

const SafeAgreementContent = ({ html, className }) => {
    if (typeof DOMParser === 'undefined') return <div className={className}>{html}</div>;
    const documentNode = new DOMParser().parseFromString(String(html || ''), 'text/html');
    const content = Array.from(documentNode.body.childNodes).map((node, index) => (
        renderAgreementNode(node, `agreement-${index}`)
    ));
    return <div className={className}>{content}</div>;
};

const SecretVisibilityButton = ({ visible, onToggle, label, disabled = false, className = '' }) => (
    <button
        type="button"
        onClick={onToggle}
        disabled={disabled}
        aria-label={`${visible ? '隐藏' : '显示'}${label}`}
        aria-pressed={visible}
        title={`${visible ? '隐藏' : '显示'}${label}`}
        className={`inline-flex h-8 w-8 items-center justify-center rounded-lg text-slate-400 transition-colors hover:text-emerald-500 disabled:cursor-not-allowed disabled:opacity-40 ${className}`}
    >
        {visible ? <EyeOff size={16} /> : <Eye size={16} />}
    </button>
);

const maskMultilineSecret = (value) => Array.from(String(value || ''), character => (
    character === '\n' || character === '\r' ? character : '•'
)).join('');

const MaskedTextarea = ({
    revealed,
    maskLabel,
    maskClassName,
    value,
    className,
    style,
    onScroll,
    ...textareaProps
}) => {
    const maskRef = useRef(null);
    const masked = !revealed && Boolean(value);
    const handleScroll = (event) => {
        if (maskRef.current) {
            maskRef.current.scrollTop = event.currentTarget.scrollTop;
            maskRef.current.scrollLeft = event.currentTarget.scrollLeft;
        }
        onScroll?.(event);
    };

    return (
        <div className="relative">
            <textarea
                {...textareaProps}
                value={value}
                onScroll={handleScroll}
                className={`${className || ''} ${masked ? 'selection:text-transparent' : ''}`}
                style={masked ? {
                    ...style,
                    color: 'transparent',
                    caretColor: '#10b981',
                    WebkitTextFillColor: 'transparent',
                } : style}
            />
            {masked && (
                <div
                    ref={maskRef}
                    aria-hidden="true"
                    data-sensitive-mask={maskLabel}
                    className={`pointer-events-none absolute inset-px overflow-hidden whitespace-pre-wrap break-all ${maskClassName}`}
                >
                    {maskMultilineSecret(value)}
                </div>
            )}
        </div>
    );
};

const RechargeView = ({ darkMode, showNotification }) => {
    const [activeTab, setActiveTab] = useState('submit'); // 'submit' | 'lookup' | 'billing' | 'guide'

    // --- 提交表单状态 ---
    const [cdkInput, setCdkInput] = useState('');
    const [validatingCdk, setValidatingCdk] = useState(false);
    const [cdkInfo, setCdkInfo] = useState(null); // CDK 校验结果
    const cdkValidationRequestId = useRef(0);
    const cdkValidationController = useRef(null);
    const [tokenInput, setTokenInput] = useState('');
    const [accountEmail, setAccountEmail] = useState('');
    const accountEmailRef = useRef('');
    const autoFilledEmailRef = useRef('');
    const [isRenewal, setIsRenewal] = useState(false);
    const [agreeTerms, setAgreeTerms] = useState(false);
    const [emailConfirmed, setEmailConfirmed] = useState(false);
    const [submittingTask, setSubmittingTask] = useState(false);
    const [createdTask, setCreatedTask] = useState(null);
    const [parsedSessionInfo, setParsedSessionInfo] = useState(null);
    const [copiedKey, setCopiedKey] = useState('');
    const [revealedSecrets, setRevealedSecrets] = useState({});

    // 平均耗时
    const [systemConfig, setSystemConfig] = useState(null);
    const [avgTimes, setAvgTimes] = useState([]);
    const [metadataError, setMetadataError] = useState('');

    // 协议弹窗
    const [showAgreementModal, setShowAgreementModal] = useState(false);
    const [agreementContent, setAgreementContent] = useState('');

    // --- 查询工作台状态 ---
    const [lookupMode, setLookupMode] = useState('single'); // 'single' | 'batch'
    const [lookupCdk, setLookupCdk] = useState('');
    const [lookupLoading, setLookupLoading] = useState(false);
    const [singleTaskResult, setSingleTaskResult] = useState(null);
    const [singleTaskQuery, setSingleTaskQuery] = useState('');
    const [invoiceDownloading, setInvoiceDownloading] = useState(false);
    const lookupRequestId = useRef(0);
    const lookupController = useRef(null);
    const [batchCdkText, setBatchCdkText] = useState('');
    const [batchResults, setBatchResults] = useState([]);
    const batchLookupRequestId = useRef(0);
    const batchLookupController = useRef(null);

    // 操作确认弹窗 (撤回/关闭)
    const [actionModal, setActionModal] = useState(null); // { type, task, redeemCode, email }
    const [actionLoading, setActionLoading] = useState(false);
    const actionRequestInFlight = useRef(false);

    // --- 账单工作台状态 ---
    const [billingToken, setBillingToken] = useState('');
    const [billingLoading, setBillingLoading] = useState(false);
    const [billingResult, setBillingResult] = useState(null);
    const [billingActionLoading, setBillingActionLoading] = useState(false);
    const billingRequestId = useRef(0);
    const billingController = useRef(null);

    const features = systemConfig?.features || {};
    const renewalEnabled = features.renewal_enabled !== false;
    // Live capability must be explicit: the backend rejects renewal unless the
    // validated CDK response contains is_renewal_supported=true.
    const renewalSupported = renewalEnabled && cdkInfo?.is_renewal_supported === true;
    const batchLookupEnabled = features.batch_lookup_enabled !== false;

    const toggleSecretVisibility = (field) => {
        setRevealedSecrets(current => ({ ...current, [field]: !current[field] }));
    };

    // 加载基础数据
    useEffect(() => {
        const controller = new AbortController();
        loadMetadata(controller.signal);
        return () => {
            controller.abort();
            cdkValidationRequestId.current += 1;
            cdkValidationController.current?.abort();
        };
    }, []);

    const loadMetadata = async (signal) => {
        try {
            const configRes = await api.getRechargeConfig({ signal });
            if (signal?.aborted) return;
            if (configRes?.data) setSystemConfig(configRes.data);
            if (configRes?.data?.mode === 'disabled') return;
            const timesRequest = configRes?.data?.mode === 'mock'
                ? api.getRechargeAvgTime('gpt', 'card', { signal })
                : Promise.resolve({ success: true, data: [] });
            const [timesResult, agreementResult] = await Promise.allSettled([
                timesRequest,
                api.getRechargeAgreement({ signal }),
            ]);
            if (signal?.aborted) return;
            if (timesResult.status === 'fulfilled') {
                setAvgTimes(Array.isArray(timesResult.value?.data) ? timesResult.value.data : []);
            } else {
                console.error('加载平均耗时失败:', timesResult.reason);
                setAvgTimes([]);
            }
            if (agreementResult.status === 'fulfilled' && agreementResult.value?.data?.content) {
                setAgreementContent(agreementResult.value.data.content);
                setMetadataError('');
            } else {
                setMetadataError(
                    agreementResult.status === 'rejected'
                        ? '充值服务协议加载失败，请刷新后重试'
                        : '服务协议内容为空，请刷新后重试',
                );
            }
        } catch (err) {
            if (signal?.aborted) return;
            console.error('加载元数据失败:', err);
            setMetadataError('充值服务协议加载失败，请刷新后重试');
        }
    };

    // 自动轮询查询中的活跃任务 (pending/processing)
    const lookupKey = singleTaskResult?.task_no;
    const lookupStatus = singleTaskResult?.status;
    useEffect(() => {
        if (activeTab !== 'lookup' || lookupMode !== 'single' || !lookupKey ||
            !['pending', 'processing', 'unknown'].includes(lookupStatus)) return;
        let canceled = false;
        let timer;
        let controller;
        let failures = 0;
        const pollStartedAt = Date.now();
        const requestId = lookupRequestId.current;
        const poll = async () => {
            if (Date.now() - pollStartedAt >= RECHARGE_POLL_MAX_DURATION_MS) {
                if (!canceled) {
                    showNotification?.('任务核对已超过 30 分钟，请稍后手动查询或联系客服。', 'error');
                }
                return;
            }
            controller = new AbortController();
            let nextStatus = lookupStatus;
            try {
                const response = await api.lookupRechargeTask(lookupKey, { signal: controller.signal });
                if (canceled || lookupRequestId.current !== requestId) return;
                if (!response.success || !response.data) throw new Error('任务查询失败');
                nextStatus = response.data.status;
                failures = 0;
                setSingleTaskResult(response.data);
            } catch (error) {
                if (canceled || controller.signal.aborted || lookupRequestId.current !== requestId) return;
                failures += 1;
                if (error.status === 401 || error.status === 403 || failures >= 3) {
                    showNotification?.('自动核对暂不可用，请稍后手动查询；不要重复提交充值。', 'error');
                    return;
                }
            }
            if (!canceled && ['pending', 'processing', 'unknown'].includes(nextStatus)) {
                const delay = Math.min(5000 * 2 ** failures, 30000);
                if (Date.now() - pollStartedAt + delay >= RECHARGE_POLL_MAX_DURATION_MS) {
                    showNotification?.('任务核对已超过 30 分钟，请稍后手动查询或联系客服。', 'error');
                    return;
                }
                timer = setTimeout(poll, delay);
            }
        };
        timer = setTimeout(poll, 5000);
        return () => {
            canceled = true;
            clearTimeout(timer);
            controller?.abort();
        };
    }, [activeTab, lookupMode, lookupKey, lookupStatus]);

    useEffect(() => {
        return () => {
            if (activeTab === 'lookup' && lookupMode === 'single') {
                lookupRequestId.current += 1;
                lookupController.current?.abort();
                setLookupLoading(false);
            }
            if (activeTab === 'lookup' && lookupMode === 'batch') {
                batchLookupRequestId.current += 1;
                batchLookupController.current?.abort();
                setLookupLoading(false);
            }
        };
    }, [activeTab, lookupMode]);

    useEffect(() => {
        if (!batchLookupEnabled && lookupMode === 'batch') setLookupMode('single');
    }, [batchLookupEnabled, lookupMode]);

    useEffect(() => {
        if (!renewalSupported && isRenewal) setIsRenewal(false);
    }, [renewalSupported, isRenewal]);

    useEffect(() => {
        setRevealedSecrets({});
    }, [activeTab]);

    useEffect(() => {
        setBillingResult(null);
        setBillingLoading(false);
        if (activeTab !== 'billing') {
            setBillingToken('');
        }
        return () => {
            billingRequestId.current += 1;
            billingController.current?.abort();
        };
    }, [activeTab, billingToken]);

    // 复制剪贴板
    const copyToClipboard = async (text, key) => {
        if (!text) return;
        try {
            if (navigator.clipboard?.writeText) {
                await navigator.clipboard.writeText(text);
            } else {
                const textarea = document.createElement('textarea');
                textarea.value = text;
                textarea.setAttribute('readonly', '');
                textarea.style.position = 'fixed';
                textarea.style.opacity = '0';
                document.body.appendChild(textarea);
                textarea.select();
                const copied = document.execCommand('copy');
                textarea.remove();
                if (!copied) throw new Error('clipboard unavailable');
            }
            setCopiedKey(key);
            setTimeout(() => setCopiedKey(''), 2000);
            showNotification?.('已复制到剪贴板', 'success');
        } catch {
            showNotification?.('复制失败，请手动复制', 'error');
        }
    };

    // 1. 验证 CDK 卡密
    const handleValidateCdk = async () => {
        const code = cdkInput.trim();
        if (!code) {
            showNotification?.('请输入 CDK 卡密', 'error');
            return;
        }
        const requestId = ++cdkValidationRequestId.current;
        cdkValidationController.current?.abort();
        const controller = new AbortController();
        cdkValidationController.current = controller;
        setCdkInfo(null);
        setIsRenewal(false);
        setValidatingCdk(true);
        try {
            const res = await api.validateRedeemCode(code, { signal: controller.signal });
            if (controller.signal.aborted || requestId !== cdkValidationRequestId.current || cdkInput.trim() !== code) return;
            if (res.success && res.data) {
                setCdkInfo(res.data);
                showNotification?.(`CDK 验证成功：${res.data.plan_name || res.data.plan_type}`, 'success');
            } else {
                showNotification?.(res.message || 'CDK 验证失败', 'error');
            }
        } catch (err) {
            if (!controller.signal.aborted && requestId === cdkValidationRequestId.current) {
                showNotification?.(err.message || 'CDK 验证失败，请检查卡密有效性', 'error');
            }
        } finally {
            if (requestId === cdkValidationRequestId.current) setValidatingCdk(false);
        }
    };

    // 智能解析 Token / Session JSON
    useEffect(() => {
        const input = tokenInput.trim();
        const previousAutoEmail = autoFilledEmailRef.current;
        const currentEmail = accountEmailRef.current;
        const updateParsedEmail = (parsedEmail) => {
            const nextEmail = String(parsedEmail || '').trim();
            const mayReplace = !currentEmail || (previousAutoEmail && currentEmail === previousAutoEmail);
            if (mayReplace) {
                setAccountEmail(nextEmail);
                accountEmailRef.current = nextEmail;
                autoFilledEmailRef.current = nextEmail;
            } else {
                autoFilledEmailRef.current = '';
            }
            // Any credential change requires a fresh ownership confirmation.
            setEmailConfirmed(false);
        };
        const clearAutoFilledEmail = () => {
            if (previousAutoEmail && currentEmail === previousAutoEmail) {
                setAccountEmail('');
                accountEmailRef.current = '';
            }
            autoFilledEmailRef.current = '';
            setEmailConfirmed(false);
        };
        if (!input) {
            setParsedSessionInfo(null);
            clearAutoFilledEmail();
            return;
        }

        // 尝试解析 ChatGPT /api/auth/session JSON
        if (input.startsWith('{') && input.endsWith('}')) {
            try {
                const data = JSON.parse(input);
                const email = data.user?.email || '';
                const planType = data.account?.planType || data.account?.plan_type || 'free';
                const hasAccessToken = Boolean(data.accessToken);

                setParsedSessionInfo({
                    isSessionJson: true,
                    email,
                    planType,
                    hasAccessToken,
                    valid: hasAccessToken && Boolean(email)
                });

                if (email) updateParsedEmail(email);
                else clearAutoFilledEmail();
                return;
            } catch {
                // 非合法 JSON
            }
        }

        // 尝试解析 邮箱----sessionKey
        if (input.includes('----')) {
            const parts = input.split('----');
            if (parts.length >= 2 && parts[0].includes('@')) {
                setParsedSessionInfo({
                    isClaudeSk: true,
                    email: parts[0].trim(),
                    sk: parts[1].trim(),
                    valid: true
                });
                updateParsedEmail(parts[0].trim());
                return;
            }
        }

        setParsedSessionInfo(null);
        clearAutoFilledEmail();
    }, [tokenInput]);

    // 提交充值任务
    const handleSubmitTask = async () => {
        if (!cdkInfo) {
            showNotification?.('请先验证有效的 CDK 卡密', 'error');
            return;
        }
        if (!agreementContent) {
            showNotification?.(metadataError || '服务协议尚未加载，请刷新后重试', 'error');
            return;
        }
        const submitCdk = cdkInput.trim();
        const submitToken = tokenInput.trim();
        const submitPlan = cdkInfo.plan_type;
        const submitEmail = accountEmail.trim();
        const submitRenewal = renewalSupported ? isRenewal : false;
        if (!tokenInput.trim() && cdkInfo.plan_type !== 'FINISHED') {
            showNotification?.('请提供充值凭证或 Session JSON', 'error');
            return;
        }
        if (!EMAIL_PATTERN.test(submitEmail)) {
            showNotification?.('请提供有效的账号接收邮箱', 'error');
            return;
        }
        if (!emailConfirmed) {
            showNotification?.('请核对并确认账号邮箱无误', 'error');
            return;
        }
        if (!agreeTerms) {
            showNotification?.('请阅读并勾选同意服务协议', 'error');
            return;
        }

        setSubmittingTask(true);
        try {
            // 1. 获取防刷 challenge
            const challengeRes = await api.getSubmissionChallenge(submitCdk, {
                token_input: submitToken,
                plan_type: submitPlan,
                is_renewal: submitRenewal
            });
            const challengeToken = challengeRes?.data?.challenge_token;
            if (!challengeRes?.success || !challengeToken) {
                throw new Error(challengeRes?.message || '获取校验令牌失败，请重试');
            }

            // 2. 提交任务
            const taskPayload = {
                redeem_code: submitCdk,
                token_input: submitToken,
                plan_type: submitPlan,
                account_email: submitEmail,
                agreement_accepted: true,
                email_verified: true,
                challenge_token: challengeToken,
                is_renewal: submitRenewal,
                acknowledge_non_free: true,
                notify_channel: 'site',
                notify_email: submitEmail
            };

            const res = await api.createRechargeTask(taskPayload);
            if (res.success && res.data) {
                setCreatedTask(res.data);
                setTokenInput('');
                setRevealedSecrets(current => ({ ...current, credential: false }));
                setParsedSessionInfo(null);
                setAccountEmail('');
                accountEmailRef.current = '';
                autoFilledEmailRef.current = '';
                setEmailConfirmed(false);
                setAgreeTerms(false);
                showNotification?.(`任务已创建！任务编号：${res.data.task_no}`, 'success');
            } else {
                showNotification?.(res.message || '提交任务失败', 'error');
            }
        } catch (err) {
            showNotification?.(
                err.status === 408 || err.status === 502
                    ? '提交结果暂未确认，请先查询任务状态，勿重复提交。'
                    : (err.message || '提交任务失败，请检查数据契约'),
                'error',
            );
        } finally {
            setSubmittingTask(false);
        }
    };

    // 2. 单个任务查询
    const handleSingleLookup = async (overrideCode) => {
        const code = (typeof overrideCode === 'string' ? overrideCode : lookupCdk).trim();
        if (!code) {
            showNotification?.('请输入查询卡密或任务编号', 'error');
            return;
        }
        const requestId = ++lookupRequestId.current;
        lookupController.current?.abort();
        batchLookupRequestId.current += 1;
        batchLookupController.current?.abort();
        const controller = new AbortController();
        lookupController.current = controller;
        setSingleTaskResult(null);
        setSingleTaskQuery(code);
        setLookupLoading(true);
        try {
            const res = await api.lookupRechargeTask(code, { signal: controller.signal });
            if (lookupRequestId.current !== requestId || controller.signal.aborted) return;
            if (res.success && res.data) {
                setSingleTaskResult(res.data);
                showNotification?.('任务状态已更新', 'success');
            } else {
                showNotification?.(res.message || '未查找到关联任务', 'error');
            }
        } catch (err) {
            if (!controller.signal.aborted && lookupRequestId.current === requestId) {
                showNotification?.(err.message || '查询失败', 'error');
            }
        } finally {
            if (lookupRequestId.current === requestId) setLookupLoading(false);
        }
    };

    // 批量卡密查询
    const handleBatchLookup = async () => {
        const text = batchCdkText.trim();
        if (!text) {
            showNotification?.('请输入待批量查询的卡密列表', 'error');
            return;
        }
        const codes = Array.from(new Set(
            text
                .split(/[\n,;\s]+/)
                .map(c => c.trim())
                .filter(Boolean),
        ));

        if (codes.length === 0) {
            showNotification?.('未检测到有效卡密', 'error');
            return;
        }
        if (codes.some(code => code.length < 4 || code.length > 120)) {
            showNotification?.('每个 CDK 卡密长度须在 4-120 字符之间', 'error');
            return;
        }
        if (codes.length > 50) {
            showNotification?.('单次最多支持查询 50 个卡密', 'error');
            return;
        }

        const requestId = ++batchLookupRequestId.current;
        batchLookupController.current?.abort();
        lookupRequestId.current += 1;
        lookupController.current?.abort();
        const controller = new AbortController();
        batchLookupController.current = controller;
        setBatchResults([]);
        setLookupLoading(true);
        try {
            const res = await api.lookupBatchRechargeTasks(codes, { signal: controller.signal });
            if (controller.signal.aborted || requestId !== batchLookupRequestId.current) return;
            if (res.success && Array.isArray(res.data)) {
                setBatchResults(res.data);
                showNotification?.(`批量查询完成：共 ${res.data.length} 条记录`, 'success');
            } else {
                showNotification?.(res.message || '批量查询失败', 'error');
            }
        } catch (err) {
            if (!controller.signal.aborted && requestId === batchLookupRequestId.current) {
                showNotification?.(err.message || '批量查询失败', 'error');
            }
        } finally {
            if (requestId === batchLookupRequestId.current) setLookupLoading(false);
        }
    };

    const saveInvoiceBlob = (blob, filename) => {
        const downloadUrl = URL.createObjectURL(blob);
        const link = document.createElement('a');
        link.href = downloadUrl;
        link.download = filename;
        document.body.appendChild(link);
        link.click();
        link.remove();
        // Defer revocation until the browser has started consuming the blob.
        window.setTimeout(() => URL.revokeObjectURL(downloadUrl), 1000);
    };

    const handleInvoiceDownload = async () => {
        const redeemCode = singleTaskQuery.trim();
        if (!redeemCode || /^(TK-)/i.test(redeemCode) || !singleTaskResult?.task_no) {
            showNotification?.('请使用完整卡密查询后下载对账凭证', 'error');
            return;
        }
        setInvoiceDownloading(true);
        try {
            const blob = await api.downloadRechargeInvoice(redeemCode);
            saveInvoiceBlob(blob, `receipt-${singleTaskResult?.task_no || 'recharge'}.txt`);
        } catch (err) {
            showNotification?.(err.message || '下载对账凭证失败', 'error');
        } finally {
            setInvoiceDownloading(false);
        }
    };

    const handleBillingInvoiceDownload = async (slug, invoiceId) => {
        if (!slug) return;
        setInvoiceDownloading(true);
        try {
            const blob = await api.downloadRechargeInvoice(slug, 'slug');
            saveInvoiceBlob(blob, `receipt-${invoiceId || slug}.txt`);
        } catch (err) {
            showNotification?.(err.message || '下载收据失败', 'error');
        } finally {
            setInvoiceDownloading(false);
        }
    };

    const openTaskAction = (type) => {
        if (!singleTaskResult?.task_no) {
            showNotification?.('当前查询结果缺少任务编号，无法执行操作', 'error');
            return;
        }
        const queriedRedeemCode = /^(TK-)/i.test(singleTaskQuery.trim()) ? '' : singleTaskQuery.trim();
        setRevealedSecrets(current => ({ ...current, actionCdk: false }));
        setActionModal({
            type,
            task: singleTaskResult,
            redeemCode: queriedRedeemCode,
            email: '',
        });
    };

    // 确认执行任务撤回 / 关闭操作（严格写操作二次确认）
    const handleConfirmTaskAction = async () => {
        if (!actionModal || actionRequestInFlight.current) return;
        const action = actionModal;
        const { type } = action;
        const taskNo = action.task?.task_no;
        const redeemCode = action.redeemCode.trim();
        const email = action.email.trim();
        if (!taskNo) {
            showNotification?.('当前任务缺少任务编号，请重新查询后再操作', 'error');
            return;
        }
        if (!redeemCode || !EMAIL_PATTERN.test(email)) {
            showNotification?.('请输入完整卡密和账号邮箱后再确认操作', 'error');
            return;
        }
        actionRequestInFlight.current = true;
        setActionLoading(true);
        try {
            let res;
            if (type === 'recall') {
                res = await api.recallRechargeTask(redeemCode, email, true, taskNo);
                if (res.success) {
                    showNotification?.('任务已成功撤回，可重新修改凭证后提交', 'success');
                    setSingleTaskResult(res.data);
                }
            } else if (type === 'close') {
                res = await api.closeRechargeTask(redeemCode, email, true, taskNo);
                if (res.success) {
                    showNotification?.('任务已关闭并销毁卡密', 'success');
                    setSingleTaskResult(res.data);
                }
            }
            if (res?.success) {
                setActionModal(null);
            } else {
                showNotification?.(res?.message || '操作未完成，请重新核对任务与卡密', 'error');
            }
        } catch (err) {
            showNotification?.(err.message || '操作失败', 'error');
        } finally {
            actionRequestInFlight.current = false;
            setActionLoading(false);
        }
    };

    // 3. 账单与订阅查询
    const handleQueryBilling = async ({ allowDuringMutation = false } = {}) => {
        if (billingActionLoading && !allowDuringMutation) return;
        const token = billingToken.trim();
        if (!token) {
            showNotification?.('请输入 ChatGPT Session Token 或凭证', 'error');
            return;
        }
        const requestId = ++billingRequestId.current;
        billingController.current?.abort();
        const controller = new AbortController();
        billingController.current = controller;
        setBillingResult(null);
        setBillingLoading(true);
        try {
            const res = await api.queryBilling(token, { signal: controller.signal });
            if (controller.signal.aborted || requestId !== billingRequestId.current) return;
            if (res.success && res.data) {
                setBillingResult(res.data);
                showNotification?.('账单与订阅状态已获取', 'success');
            } else {
                showNotification?.(res.message || '查询账单失败', 'error');
            }
        } catch (err) {
            if (!controller.signal.aborted && requestId === billingRequestId.current) {
                showNotification?.(err.message || '查询账单异常', 'error');
            }
        } finally {
            if (requestId === billingRequestId.current) setBillingLoading(false);
        }
    };

    // 取消自动续费
    const handleCancelSubscription = async () => {
        const token = billingToken.trim();
        if (!token) return;
        if (!window.confirm('确认取消当前账号的自动续费？当前会员权益将保留至本周期结束。')) return;

        const requestId = billingRequestId.current;
        setBillingActionLoading(true);
        try {
            const res = await api.cancelSubscription(token, true);
            if (requestId !== billingRequestId.current) return;
            if (res.success) {
                showNotification?.('已成功取消自动续费', 'success');
                await handleQueryBilling({ allowDuringMutation: true });
            }
        } catch (err) {
            showNotification?.(err.message || '取消续费失败', 'error');
        } finally {
            setBillingActionLoading(false);
        }
    };

    // 恢复自动续费
    const handleResumeSubscription = async () => {
        const token = billingToken.trim();
        if (!token) return;
        if (!window.confirm('确认恢复自动续费？系统将重新启用自动扣款与账单推送服务。')) return;

        const requestId = billingRequestId.current;
        setBillingActionLoading(true);
        try {
            const res = await api.resumeSubscription(token, true);
            if (requestId !== billingRequestId.current) return;
            if (res.success) {
                showNotification?.('已成功恢复自动续费', 'success');
                await handleQueryBilling({ allowDuringMutation: true });
            }
        } catch (err) {
            showNotification?.(err.message || '恢复续费失败', 'error');
        } finally {
            setBillingActionLoading(false);
        }
    };

    return (
        <div className="space-y-6 animate-in fade-in duration-300">
            {/* 模式禁用警告条 */}
            {systemConfig?.mode === 'disabled' && (
                <div className="p-4 rounded-2xl bg-rose-500/10 border border-rose-500/30 text-rose-600 dark:text-rose-400 text-xs flex items-center gap-2">
                    <AlertTriangle size={16} className="flex-shrink-0" />
                    <span>充值交付功能当前在服务端未启用 (RECHARGE_MODE=disabled)，提交与写操作将被系统拦截。</span>
                </div>
            )}
            {metadataError && systemConfig?.mode !== 'disabled' && (
                <div className="p-4 rounded-2xl bg-amber-500/10 border border-amber-500/30 text-amber-600 dark:text-amber-400 text-xs flex items-center gap-2">
                    <AlertTriangle size={16} className="flex-shrink-0" />
                    <span>{metadataError}</span>
                </div>
            )}

            {/* 顶栏与标签导航 */}
            <div className={`p-6 rounded-3xl border shadow-sm backdrop-blur-md transition-all ${
                darkMode ? 'bg-slate-800/80 border-slate-700/80 text-slate-100' : 'bg-white/90 border-slate-200/80 text-slate-800'
            }`}>
                <div className="flex flex-col md:flex-row justify-between items-start md:items-center gap-4">
                    <div className="flex items-center gap-3.5">
                        <div className="p-3 bg-gradient-to-tr from-emerald-600 to-teal-500 rounded-2xl text-white shadow-lg shadow-emerald-500/20">
                            <CreditCard size={26} />
                        </div>
                        <div>
                            <div className="flex items-center gap-2">
                                <h1 className="text-xl font-bold tracking-tight">自助充值与订单服务</h1>
                                <span className="text-xs px-2.5 py-0.5 rounded-full font-semibold bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border border-emerald-500/20">
                                    AI Chong 666 互通
                                </span>
                                {systemConfig?.mode === 'live' && (
                                    <span className="text-xs px-2.5 py-0.5 rounded-full font-semibold bg-emerald-500/15 text-emerald-600 dark:text-emerald-400 border border-emerald-500/30">
                                        生产直连 (Live)
                                    </span>
                                )}
                                {systemConfig?.mode === 'mock' && (
                                    <span className="text-xs px-2.5 py-0.5 rounded-full font-semibold bg-amber-500/15 text-amber-600 dark:text-amber-400 border border-amber-500/30">
                                        沙箱模拟 (Mock)
                                    </span>
                                )}
                                {systemConfig?.mode === 'disabled' && (
                                    <span className="text-xs px-2.5 py-0.5 rounded-full font-semibold bg-rose-500/15 text-rose-600 dark:text-rose-400 border border-rose-500/30">
                                        服务未启用 (Disabled)
                                    </span>
                                )}
                            </div>
                            <p className={`text-xs mt-1 ${darkMode ? 'text-slate-400' : 'text-slate-500'}`}>
                                使用 CDK 提交充值，查询订单进度并管理账号续费状态
                            </p>
                        </div>
                    </div>

                    {/* 标签切换按钮组 */}
                    <div className={`flex gap-1 p-1 rounded-2xl border shadow-inner ${
                        darkMode ? 'bg-slate-900 border-slate-700/60' : 'bg-slate-100/90 border-slate-200/60'
                    }`}>
                        {[
                            { id: 'submit', label: '充值提交', icon: Zap },
                            { id: 'lookup', label: '进度查询', icon: Search },
                            { id: 'billing', label: '账单续费', icon: Layers },
                            { id: 'guide', label: '提取教程', icon: HelpCircle }
                        ].map(tab => {
                            const Icon = tab.icon;
                            const isActive = activeTab === tab.id;
                            return (
                                <button
                                    key={tab.id}
                                    onClick={() => setActiveTab(tab.id)}
                                    className={`flex items-center gap-2 px-3.5 py-2 rounded-xl text-xs font-semibold transition-all ${
                                        isActive
                                            ? darkMode
                                                ? 'bg-slate-800 text-emerald-400 shadow-sm border border-slate-700'
                                                : 'bg-white text-emerald-600 shadow-sm'
                                            : darkMode
                                                ? 'text-slate-400 hover:text-slate-200'
                                                : 'text-slate-600 hover:text-slate-900'
                                    }`}
                                >
                                    <Icon size={14} />
                                    <span>{tab.label}</span>
                                </button>
                            );
                        })}
                    </div>
                </div>

                {/* 平均耗时展示条 */}
                {systemConfig?.mode === 'mock' && avgTimes.length > 0 && (
                    <div className={`mt-5 pt-4 border-t flex flex-wrap items-center gap-2 text-xs ${
                        darkMode ? 'border-slate-700/60 text-slate-400' : 'border-slate-100 text-slate-500'
                    }`}>
                        <div className="flex items-center gap-1.5 font-medium mr-2 text-emerald-600 dark:text-emerald-400">
                            <Clock size={14} />
                            <span>沙箱参考耗时（非实时）：</span>
                        </div>
                        {avgTimes.map(item => (
                            <span
                                key={item.plan_type}
                                className={`px-2.5 py-1 rounded-lg border font-mono ${
                                    darkMode ? 'bg-slate-900/60 border-slate-700/80 text-slate-300' : 'bg-slate-50 border-slate-200 text-slate-700'
                                }`}
                            >
                                {item.plan_name}: <strong className="text-emerald-500">{Math.round(item.avg_seconds / 60)} 分钟</strong>
                            </span>
                        ))}
                    </div>
                )}
            </div>

            {/* ==================== TAB 1: 充值提交 ==================== */}
            {activeTab === 'submit' && (
                <div className="grid grid-cols-1 lg:grid-cols-12 gap-6">
                    <div className="lg:col-span-8 space-y-6">
                        {/* 步骤 1: CDK 卡密验证 */}
                        <div className={`p-6 rounded-3xl border shadow-sm transition-all ${
                            darkMode ? 'bg-slate-800 border-slate-700' : 'bg-white border-slate-200'
                        }`}>
                            <div className="flex items-center mb-4">
                                <div className="flex items-center gap-2.5 font-bold text-base">
                                    <span className="w-6 h-6 rounded-full bg-emerald-500 text-white flex items-center justify-center text-xs">
                                        1
                                    </span>
                                    <span>验证 CDK 卡密</span>
                                </div>
                                {cdkInfo && (
                                    <span className="px-3 py-1 text-xs font-semibold rounded-full bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border border-emerald-500/20 flex items-center gap-1">
                                        <CheckCircle2 size={13} />
                                        卡密已核验有效
                                    </span>
                                )}
                            </div>

                            <div className="flex gap-3">
                                <div className="relative flex-1">
                                    <input
                                        type={revealedSecrets.submitCdk ? 'text' : 'password'}
                                        value={cdkInput}
                                        onChange={e => {
                                            const value = e.target.value;
                                            cdkValidationRequestId.current += 1;
                                            cdkValidationController.current?.abort();
                                            setValidatingCdk(false);
                                            setCdkInput(value);
                                            setCdkInfo(null);
                                            setCreatedTask(null);
                                            setIsRenewal(false);
                                        }}
                                        placeholder="请输入 4-120 字符的 CDK 卡密（如 PLUS-XXXX-XXXX）"
                                        maxLength={120}
                                        disabled={submittingTask}
                                        autoComplete="off"
                                        spellCheck={false}
                                        className={`w-full px-4 py-3 pr-12 rounded-2xl border text-sm font-mono outline-none transition-all ${
                                            darkMode
                                                ? 'bg-slate-900 border-slate-700 text-slate-100 focus:border-emerald-500'
                                                : 'bg-slate-50 border-slate-200 text-slate-900 focus:border-emerald-500'
                                        }`}
                                    />
                                    <SecretVisibilityButton
                                        visible={Boolean(revealedSecrets.submitCdk)}
                                        onToggle={() => toggleSecretVisibility('submitCdk')}
                                        label="CDK 卡密"
                                        disabled={submittingTask}
                                        className="absolute right-2 top-1/2 -translate-y-1/2"
                                    />
                                </div>
                                <button
                                    onClick={handleValidateCdk}
                                    disabled={submittingTask || validatingCdk || !cdkInput.trim()}
                                    className="px-6 py-3 rounded-2xl text-sm font-bold text-white bg-gradient-to-r from-emerald-600 to-teal-600 hover:from-emerald-500 hover:to-teal-500 disabled:opacity-50 transition-all shadow-md shadow-emerald-600/20 flex items-center gap-2 flex-shrink-0"
                                >
                                    {validatingCdk ? <RefreshCw size={16} className="animate-spin" /> : <Zap size={16} />}
                                    <span>验证卡密</span>
                                </button>
                            </div>

                            {cdkInfo && (
                                <div className={`mt-4 p-4 rounded-2xl border flex flex-wrap items-center justify-between gap-3 text-xs ${
                                    darkMode ? 'bg-slate-900/60 border-slate-700 text-slate-300' : 'bg-emerald-50/50 border-emerald-200/60 text-slate-700'
                                }`}>
                                    <div>
                                        <span className="text-slate-400">套餐类别：</span>
                                        <strong className="text-emerald-600 dark:text-emerald-400 text-sm ml-1">
                                            {cdkInfo.plan_name || cdkInfo.plan_type}
                                        </strong>
                                    </div>
                                    <div>
                                        <span className="text-slate-400">交付类型：</span>
                                        <span className="font-semibold ml-1">{cdkInfo.product === 'claude_code' ? 'Claude 额度' : 'ChatGPT 官方'}</span>
                                    </div>
                                    <div>
                                        <span className="text-slate-400">换绑状态：</span>
                                        <span className="font-semibold ml-1">{cdkInfo.account_change_locked ? '已锁定账号' : '自由绑定'}</span>
                                    </div>
                                </div>
                            )}
                        </div>

                        {/* 步骤 2: 凭证与账号录入 */}
                        <div className={`p-6 rounded-3xl border shadow-sm transition-all ${
                            darkMode ? 'bg-slate-800 border-slate-700' : 'bg-white border-slate-200'
                        } ${!cdkInfo ? 'opacity-50 pointer-events-none' : ''}`}>
                            <div className="flex items-center justify-between mb-4">
                                <div className="flex items-center gap-2.5 font-bold text-base">
                                    <span className="w-6 h-6 rounded-full bg-emerald-500 text-white flex items-center justify-center text-xs">
                                        2
                                    </span>
                                    <span>录入凭证与账号</span>
                                </div>
                            </div>

                            <div className="space-y-4">
                                <div>
                                    <div className="flex justify-between items-center mb-1.5">
                                        <label className={`text-xs font-semibold ${darkMode ? 'text-slate-300' : 'text-slate-600'}`}>
                                            充值凭证 (Session JSON / 邮箱----sessionKey / KYC 链接)
                                        </label>
                                        <div className="flex items-center gap-1">
                                            <SecretVisibilityButton
                                                visible={Boolean(revealedSecrets.credential)}
                                                onToggle={() => toggleSecretVisibility('credential')}
                                                label="充值凭证"
                                                disabled={submittingTask}
                                            />
                                            <button
                                                type="button"
                                                onClick={() => setActiveTab('guide')}
                                                className="text-xs text-emerald-500 hover:underline flex items-center gap-1"
                                            >
                                                <HelpCircle size={12} />
                                                <span>查看凭证提取教程</span>
                                            </button>
                                        </div>
                                    </div>
                                    <MaskedTextarea
                                        revealed={Boolean(revealedSecrets.credential)}
                                        maskLabel="充值凭证"
                                        maskClassName="p-3.5 text-xs font-mono text-slate-400"
                                        rows={4}
                                        value={tokenInput}
                                        onChange={e => setTokenInput(e.target.value)}
                                        maxLength={65535}
                                        disabled={submittingTask}
                                        autoComplete="off"
                                        spellCheck={false}
                                        placeholder="粘贴来自 chatgpt.com/api/auth/session 的完整 JSON，或输入 user@example.com----sk-ant-sid02-xxx"
                                        className={`w-full p-3.5 rounded-2xl border text-xs font-mono outline-none transition-all ${
                                            darkMode
                                                ? 'bg-slate-900 border-slate-700 text-slate-100 focus:border-emerald-500'
                                                : 'bg-slate-50 border-slate-200 text-slate-900 focus:border-emerald-500'
                                        }`}
                                    />

                                    {/* 智能解析结果提示 */}
                                    {parsedSessionInfo && (
                                        <div className={`mt-2.5 p-3 rounded-xl border text-xs flex items-center justify-between ${
                                            parsedSessionInfo.valid
                                                ? 'bg-emerald-500/10 border-emerald-500/30 text-emerald-600 dark:text-emerald-400'
                                                : 'bg-amber-500/10 border-amber-500/30 text-amber-600 dark:text-amber-400'
                                        }`}>
                                            <div className="flex items-center gap-2">
                                                <CheckCircle2 size={15} />
                                                <span>
                                                    已智能解析：<strong>{parsedSessionInfo.email || '未包含邮箱'}</strong>（当前套餐: {parsedSessionInfo.planType || '未知'}）
                                                </span>
                                            </div>
                                            <span className="text-[11px] font-mono opacity-80">accessToken 已验证</span>
                                        </div>
                                    )}
                                </div>

                                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                                    <div>
                                        <label className={`block text-xs font-semibold mb-1.5 ${darkMode ? 'text-slate-300' : 'text-slate-600'}`}>
                                            接收账号邮箱 <span className="text-rose-500">*</span>
                                        </label>
                                        <input
                                            type="email"
                                            value={accountEmail}
                                            onChange={e => {
                                                const value = e.target.value;
                                                accountEmailRef.current = value;
                                                autoFilledEmailRef.current = '';
                                                setAccountEmail(value);
                                                setEmailConfirmed(false);
                                            }}
                                            disabled={submittingTask}
                                            maxLength={256}
                                            placeholder="user@gmail.com"
                                            className={`w-full px-4 py-2.5 rounded-2xl border text-sm outline-none transition-all ${
                                                darkMode
                                                    ? 'bg-slate-900 border-slate-700 text-slate-100 focus:border-emerald-500'
                                                    : 'bg-slate-50 border-slate-200 text-slate-900 focus:border-emerald-500'
                                            }`}
                                        />
                                    </div>

                                    <div className="flex flex-col justify-end">
                                        {renewalEnabled && <label className={`flex items-center gap-2 p-3 rounded-2xl border cursor-pointer select-none text-xs ${
                                            darkMode ? 'bg-slate-900/60 border-slate-700 text-slate-300' : 'bg-slate-50 border-slate-200 text-slate-700'
                                        }`}>
                                            <input
                                                type="checkbox"
                                                checked={isRenewal}
                                                onChange={e => setIsRenewal(e.target.checked)}
                                                disabled={submittingTask || !renewalSupported}
                                                className="w-4 h-4 rounded text-emerald-600 focus:ring-emerald-500"
                                            />
                                            <span>仅续费模式（为已绑定卡充值续期，不添加新卡）</span>
                                        </label>}
                                    </div>
                                </div>
                            </div>
                        </div>

                        {/* 步骤 3: 契约核验与安全提交 */}
                        <div className={`p-6 rounded-3xl border shadow-sm transition-all ${
                            darkMode ? 'bg-slate-800 border-slate-700' : 'bg-white border-slate-200'
                        } ${!cdkInfo ? 'opacity-50 pointer-events-none' : ''}`}>
                            <div className="flex items-center gap-2.5 font-bold text-base mb-4">
                                <span className="w-6 h-6 rounded-full bg-emerald-500 text-white flex items-center justify-center text-xs">
                                    3
                                </span>
                                <span>协议签署与安全确认</span>
                            </div>

                            <div className="space-y-3">
                                <label className={`flex items-start gap-2.5 text-xs cursor-pointer select-none ${
                                    darkMode ? 'text-slate-300' : 'text-slate-700'
                                }`}>
                                    <input
                                        type="checkbox"
                                        checked={emailConfirmed}
                                        onChange={e => setEmailConfirmed(e.target.checked)}
                                        disabled={submittingTask}
                                        className="w-4 h-4 mt-0.5 rounded text-emerald-600 focus:ring-emerald-500"
                                    />
                                    <span>我已仔细核对充值邮箱为 <strong>{accountEmail || '(未填写)'}</strong>，确认账号所有权归属正确。</span>
                                </label>

                                <label className={`flex items-start gap-2.5 text-xs cursor-pointer select-none ${
                                    darkMode ? 'text-slate-300' : 'text-slate-700'
                                }`}>
                                    <input
                                        type="checkbox"
                                        checked={agreeTerms}
                                        onChange={e => setAgreeTerms(e.target.checked)}
                                        disabled={submittingTask || !agreementContent}
                                        className="w-4 h-4 mt-0.5 rounded text-emerald-600 focus:ring-emerald-500"
                                    />
                                    <span>
                                        我已完整阅读并同意
                                        <button
                                            type="button"
                                            onClick={() => setShowAgreementModal(true)}
                                            className="text-emerald-500 font-semibold hover:underline mx-1"
                                        >
                                            《充值用户服务协议与免责声明》
                                        </button>
                                        ，已知晓会员覆盖规则。
                                    </span>
                                </label>

                                <div className="pt-2">
                                    <button
                                        onClick={handleSubmitTask}
                                        disabled={submittingTask || !cdkInfo || !agreeTerms || !emailConfirmed}
                                        className="w-full py-3.5 rounded-2xl text-sm font-bold text-white bg-gradient-to-r from-emerald-600 via-teal-600 to-cyan-600 hover:opacity-95 disabled:opacity-50 transition-all shadow-lg shadow-emerald-600/20 flex items-center justify-center gap-2"
                                    >
                                        {submittingTask ? (
                                            <>
                                                <RefreshCw size={18} className="animate-spin" />
                                                <span>正在核验契约并创建任务...</span>
                                            </>
                                        ) : (
                                            <>
                                                <CheckCircle2 size={18} />
                                                <span>同意协议并提交充值任务</span>
                                            </>
                                        )}
                                    </button>
                                </div>
                            </div>
                        </div>
                    </div>

                    {/* 右侧：说明与最新创建任务卡片 */}
                    <div className="lg:col-span-4 space-y-6">
                        {/* 刚刚创建的任务卡片 */}
                        {createdTask ? (
                            <div className={`p-6 rounded-3xl border shadow-md ${
                                darkMode ? 'bg-emerald-950/20 border-emerald-500/40 text-slate-100' : 'bg-emerald-50/80 border-emerald-300 text-slate-800'
                            }`}>
                                <div className="flex items-center gap-2 text-emerald-600 dark:text-emerald-400 font-bold text-base mb-3">
                                    <CheckCircle2 size={20} />
                                    <span>任务提交成功！</span>
                                </div>
                                <div className="space-y-2.5 text-xs font-mono">
                                    <div className="flex justify-between">
                                        <span className="text-slate-400">流水号：</span>
                                        <span className="font-bold">{createdTask.task_no}</span>
                                    </div>
                                    <div className="flex justify-between">
                                        <span className="text-slate-400">套餐：</span>
                                        <span>{createdTask.plan_type}</span>
                                    </div>
                                    <div className="flex justify-between">
                                        <span className="text-slate-400">状态：</span>
                                        <span className="text-emerald-500 font-bold">{createdTask.status_text || createdTask.status}</span>
                                    </div>
                                    <div className="flex justify-between">
                                        <span className="text-slate-400">创建时间：</span>
                                        <span>{createdTask.created_at}</span>
                                    </div>
                                </div>

                                <div className="mt-4 pt-4 border-t border-emerald-500/20 flex gap-2">
                                    <button
                                        onClick={() => {
                                            const queryVal = createdTask.task_no;
                                            setLookupCdk(queryVal);
                                            setActiveTab('lookup');
                                            handleSingleLookup(queryVal);
                                        }}
                                        className="flex-1 py-2 rounded-xl text-xs font-bold text-white bg-emerald-600 hover:bg-emerald-500 transition-all text-center"
                                    >
                                        查看履约进度
                                    </button>
                                    <button
                                        onClick={() => copyToClipboard(createdTask.task_no, 'task_no')}
                                        className="p-2 rounded-xl border border-emerald-500/40 text-emerald-600 dark:text-emerald-400 hover:bg-emerald-500/10 transition-all"
                                        title="复制流水号"
                                    >
                                        {copiedKey === 'task_no' ? <Check size={16} /> : <Copy size={16} />}
                                    </button>
                                </div>
                            </div>
                        ) : (
                            <div className={`p-6 rounded-3xl border shadow-sm ${
                                darkMode ? 'bg-slate-800/80 border-slate-700 text-slate-300' : 'bg-white border-slate-200 text-slate-600'
                            }`}>
                                <h3 className="font-bold text-sm mb-3 flex items-center gap-2">
                                    <Shield size={16} className="text-emerald-500" />
                                    <span>安全充值交付承诺</span>
                                </h3>
                                <ul className="space-y-2 text-xs leading-relaxed text-slate-400">
                                    <li>• 请勿在模拟环境输入真实 Session 或 Cookie，提交成功后会清空输入凭证。</li>
                                    <li>• 模拟收据只用于测试，不代表真实支付；真实凭证下载暂未开放。</li>
                                    <li>• 如遇风控拦截或卡密错误，支持一键撤回并保留卡密。</li>
                                </ul>
                            </div>
                        )}
                    </div>
                </div>
            )}

            {/* ==================== TAB 2: 任务与卡密查询 ==================== */}
            {activeTab === 'lookup' && (
                <div className="space-y-6">
                    <div className={`p-6 rounded-3xl border shadow-sm transition-all ${
                        darkMode ? 'bg-slate-800 border-slate-700' : 'bg-white border-slate-200'
                    }`}>
                        <div className="flex flex-col sm:flex-row justify-between items-start sm:items-center gap-4 mb-6">
                            <div>
                                <h2 className="text-lg font-bold">任务与卡密进度工作台</h2>
                                <p className={`text-xs mt-1 ${darkMode ? 'text-slate-400' : 'text-slate-500'}`}>
                                    支持按单个 CDK 卡密/任务流水号深度查询，或多行文本一键批量检索
                                </p>
                            </div>

                            <div className={`flex gap-1 p-1 rounded-xl border ${
                                darkMode ? 'bg-slate-900 border-slate-700' : 'bg-slate-100 border-slate-200'
                            }`}>
                                <button
                                    onClick={() => setLookupMode('single')}
                                    className={`px-3.5 py-1.5 rounded-lg text-xs font-semibold transition-all ${
                                        lookupMode === 'single'
                                            ? darkMode ? 'bg-slate-800 text-emerald-400' : 'bg-white text-emerald-600 shadow-sm'
                                            : 'text-slate-400 hover:text-slate-200'
                                    }`}
                                >
                                    单个查询
                                </button>
                                {batchLookupEnabled && <button
                                    onClick={() => setLookupMode('batch')}
                                    className={`px-3.5 py-1.5 rounded-lg text-xs font-semibold transition-all ${
                                        lookupMode === 'batch'
                                            ? darkMode ? 'bg-slate-800 text-emerald-400' : 'bg-white text-emerald-600 shadow-sm'
                                            : 'text-slate-400 hover:text-slate-200'
                                    }`}
                                >
                                    批量查询 (最多50个)
                                </button>}
                            </div>
                        </div>

                        {lookupMode === 'single' ? (
                            <div className="space-y-4">
                                <div className="flex gap-3">
                                    <div className="relative flex-1">
                                    <input
                                        type={revealedSecrets.lookupCdk ? 'text' : 'password'}
                                        value={lookupCdk}
                                        onChange={e => {
                                            const value = e.target.value;
                                            setLookupCdk(value);
                                            if (value.trim() !== singleTaskQuery) {
                                                lookupRequestId.current += 1;
                                                lookupController.current?.abort();
                                                setLookupLoading(false);
                                                setSingleTaskResult(null);
                                                setSingleTaskQuery('');
                                            }
                                        }}
                                        placeholder="输入 CDK 卡密或任务编号（如 PLUS-XXXX 或 TK-2026...）"
                                        maxLength={128}
                                        autoComplete="off"
                                        spellCheck={false}
                                        className={`w-full px-4 py-3 pr-12 rounded-2xl border text-sm font-mono outline-none transition-all ${
                                            darkMode
                                                ? 'bg-slate-900 border-slate-700 text-slate-100 focus:border-emerald-500'
                                                : 'bg-slate-50 border-slate-200 text-slate-900 focus:border-emerald-500'
                                        }`}
                                    />
                                    <SecretVisibilityButton
                                        visible={Boolean(revealedSecrets.lookupCdk)}
                                        onToggle={() => toggleSecretVisibility('lookupCdk')}
                                        label="查询卡密"
                                        disabled={lookupLoading}
                                        className="absolute right-2 top-1/2 -translate-y-1/2"
                                    />
                                    </div>
                                    <button
                                        onClick={() => handleSingleLookup()}
                                        disabled={lookupLoading || !lookupCdk.trim()}
                                        className="px-6 py-3 rounded-2xl text-sm font-bold text-white bg-emerald-600 hover:bg-emerald-500 disabled:opacity-50 transition-all shadow-md shadow-emerald-600/20 flex items-center gap-2 flex-shrink-0"
                                    >
                                        {lookupLoading ? <RefreshCw size={16} className="animate-spin" /> : <Search size={16} />}
                                        <span>立即查询</span>
                                    </button>
                                </div>

                                {/* 查询结果详情卡片 */}
                                {singleTaskResult && (
                                    <div className={`mt-6 p-6 rounded-3xl border shadow-inner transition-all ${
                                        darkMode ? 'bg-slate-900/60 border-slate-700' : 'bg-slate-50 border-slate-200'
                                    }`}>
                                        <div className="flex flex-col md:flex-row justify-between items-start md:items-center gap-4 pb-4 border-b border-slate-700/40">
                                            <div>
                                                <div className="flex items-center gap-2.5">
                                                    <h3 className="font-bold text-base">{singleTaskResult.plan_type} 履约任务</h3>
                                                    <span className={`px-2.5 py-0.5 rounded-full text-xs font-bold ${
                                                        singleTaskResult.status === 'processing'
                                                            ? 'bg-blue-500/10 text-blue-500 border border-blue-500/20'
                                                            : singleTaskResult.status === 'completed'
                                                            ? 'bg-emerald-500/10 text-emerald-500 border border-emerald-500/20'
                                                            : singleTaskResult.status === 'closed'
                                                            ? 'bg-slate-500/10 text-slate-400 border border-slate-500/20'
                                                            : 'bg-amber-500/10 text-amber-500 border border-amber-500/20'
                                                    }`}>
                                                        {singleTaskResult.status_text || singleTaskResult.status}
                                                    </span>
                                                </div>
                                                <p className="text-xs text-slate-400 mt-1 font-mono">
                                                    任务编号：{singleTaskResult.task_no || '待分配'}
                                                </p>
                                            </div>

                                            {/* 操作按钮组 */}
                                            <div className="flex items-center gap-2">
                                                {['pending', 'processing'].includes(singleTaskResult.status) && (
                                                    <>
                                                        <button
                                                            onClick={() => openTaskAction('recall')}
                                                            className="px-3.5 py-2 rounded-xl text-xs font-semibold bg-amber-500/10 text-amber-600 dark:text-amber-400 border border-amber-500/30 hover:bg-amber-500/20 transition-all"
                                                        >
                                                            撤回任务
                                                        </button>
                                                        <button
                                                            onClick={() => openTaskAction('close')}
                                                            className="px-3.5 py-2 rounded-xl text-xs font-semibold bg-rose-500/10 text-rose-600 dark:text-rose-400 border border-rose-500/30 hover:bg-rose-500/20 transition-all"
                                                        >
                                                            关闭并销毁
                                                        </button>
                                                    </>
                                                )}
                                                {singleTaskResult.is_mock ? (
                                                    /^(TK-)/i.test(singleTaskQuery.trim()) || !['pending', 'processing', 'unknown', 'completed', 'failed', 'recalled', 'closed'].includes(singleTaskResult.status) ? (
                                                        <span className="text-xs text-slate-400">请使用完整卡密查询后下载对账凭证</span>
                                                    ) : (
                                                        <button
                                                            type="button"
                                                            onClick={handleInvoiceDownload}
                                                            disabled={invoiceDownloading || !singleTaskQuery.trim()}
                                                            className="px-3.5 py-2 rounded-xl text-xs font-semibold border border-slate-600 text-slate-300 hover:bg-slate-700/50 disabled:opacity-50 transition-all flex items-center gap-1.5"
                                                        >
                                                            <Download size={13} />
                                                            <span>{invoiceDownloading ? '下载中...' : '对账凭证'}</span>
                                                        </button>
                                                    )
                                                ) : <span className="text-xs text-slate-400">真实凭证下载暂未开放</span>}
                                            </div>
                                        </div>

                                        <div className="grid grid-cols-2 sm:grid-cols-3 gap-4 mt-4 text-xs">
                                            <div>
                                                <span className="text-slate-400 block mb-1">专卡尾号</span>
                                                <span className="font-mono font-semibold">{singleTaskResult.card_last4 || '****'}</span>
                                            </div>
                                            <div>
                                                <span className="text-slate-400 block mb-1">创建时间</span>
                                                <span className="font-mono">{singleTaskResult.created_at || '-'}</span>
                                            </div>
                                            <div>
                                                <span className="text-slate-400 block mb-1">更新时间</span>
                                                <span className="font-mono">{singleTaskResult.updated_at || '-'}</span>
                                            </div>
                                        </div>

                                        {singleTaskResult.notice && (
                                            <div className="mt-4 p-3 rounded-xl bg-blue-500/10 border border-blue-500/20 text-blue-400 text-xs flex items-center gap-2">
                                                <AlertCircle size={14} className="flex-shrink-0" />
                                                <span>{singleTaskResult.notice}</span>
                                            </div>
                                        )}
                                    </div>
                                )}
                            </div>
                        ) : (
                            /* 批量查询面板 */
                            <div className="space-y-4">
                                <MaskedTextarea
                                    revealed={Boolean(revealedSecrets.batchCdk)}
                                    maskLabel="批量卡密"
                                    maskClassName="p-4 text-xs font-mono text-slate-400"
                                    rows={5}
                                    value={batchCdkText}
                                    onChange={e => {
                                        batchLookupRequestId.current += 1;
                                        batchLookupController.current?.abort();
                                        setLookupLoading(false);
                                        setBatchResults([]);
                                        setBatchCdkText(e.target.value);
                                    }}
                                    autoComplete="off"
                                    spellCheck={false}
                                    placeholder="每行输入一个卡密，支持逗号或换行分隔（单次上限 50 个）&#10;PLUS-XXXX-001&#10;PRO5X-YYYY-002"
                                    className={`w-full p-4 rounded-2xl border text-xs font-mono outline-none transition-all ${
                                        darkMode
                                            ? 'bg-slate-900 border-slate-700 text-slate-100 focus:border-emerald-500'
                                            : 'bg-slate-50 border-slate-200 text-slate-900 focus:border-emerald-500'
                                    }`}
                                />
                                <div className="flex justify-between items-center">
                                    <div className="flex items-center gap-2">
                                        <SecretVisibilityButton
                                            visible={Boolean(revealedSecrets.batchCdk)}
                                            onToggle={() => toggleSecretVisibility('batchCdk')}
                                            label="批量卡密"
                                            disabled={lookupLoading}
                                        />
                                        <span className="text-xs text-slate-400">
                                            已输入 {new Set(batchCdkText.split(/[\n,;\s]+/).map(code => code.trim()).filter(Boolean)).size} / 50 个卡密
                                        </span>
                                    </div>
                                    <button
                                        onClick={handleBatchLookup}
                                        disabled={lookupLoading || !batchCdkText.trim()}
                                        className="px-6 py-2.5 rounded-2xl text-xs font-bold text-white bg-emerald-600 hover:bg-emerald-500 disabled:opacity-50 transition-all shadow-md shadow-emerald-600/20 flex items-center gap-2"
                                    >
                                        {lookupLoading ? <RefreshCw size={14} className="animate-spin" /> : <ListFilter size={14} />}
                                        <span>批量查询进度</span>
                                    </button>
                                </div>

                                {batchResults.length > 0 && (
                                    <div className="overflow-x-auto mt-4 rounded-2xl border border-slate-700">
                                        <table className="w-full text-left text-xs">
                                            <thead className={darkMode ? 'bg-slate-900 text-slate-400' : 'bg-slate-100 text-slate-600'}>
                                                <tr>
                                                    <th className="p-3">任务编号</th>
                                                    <th className="p-3">套餐</th>
                                                    <th className="p-3">当前状态</th>
                                                    <th className="p-3">创建时间</th>
                                                </tr>
                                            </thead>
                                            <tbody className="divide-y divide-slate-700/40">
                                                {batchResults.map((item, idx) => (
                                                    <tr key={item.task_no || idx} className={darkMode ? 'hover:bg-slate-750' : 'hover:bg-slate-50'}>
                                                        <td className="p-3 font-mono font-semibold">{item.task_no || '-'}</td>
                                                        <td className="p-3">{item.plan_type}</td>
                                                        <td className="p-3">
                                                            <span className={`px-2 py-0.5 rounded-md font-bold ${
                                                                item.ok ? 'bg-emerald-500/10 text-emerald-400' : 'bg-slate-700 text-slate-400'
                                                            }`}>
                                                                {item.status_text || item.status}
                                                            </span>
                                                        </td>
                                                        <td className="p-3 font-mono text-slate-400">{item.created_at}</td>
                                                    </tr>
                                                ))}
                                            </tbody>
                                        </table>
                                    </div>
                                )}
                            </div>
                        )}
                    </div>
                </div>
            )}

            {/* ==================== TAB 3: 账单续费管理 ==================== */}
            {activeTab === 'billing' && (
                <div className="space-y-6">
                    <div className={`p-6 rounded-3xl border shadow-sm transition-all ${
                        darkMode ? 'bg-slate-800 border-slate-700' : 'bg-white border-slate-200'
                    }`}>
                        <h2 className="text-lg font-bold mb-1">ChatGPT 账单与自动续费中控</h2>
                        <p className={`text-xs mb-6 ${darkMode ? 'text-slate-400' : 'text-slate-500'}`}>
                            输入已绑定账号的 Session Token，查询签约扣费专卡、下次扣款日，支持一键取消或恢复自动续费
                        </p>

                        <div className="flex gap-3">
                            <div className="relative flex-1">
                                <input
                                    type={revealedSecrets.billingToken ? 'text' : 'password'}
                                    value={billingToken}
                                    onChange={e => setBillingToken(e.target.value)}
                                    disabled={billingActionLoading}
                                    maxLength={65535}
                                    autoComplete="off"
                                    spellCheck={false}
                                    placeholder="输入账号 accessToken 或 chatgpt.com/api/auth/session 返回内容"
                                    className={`w-full px-4 py-3 pr-12 rounded-2xl border text-sm font-mono outline-none transition-all ${
                                        darkMode
                                            ? 'bg-slate-900 border-slate-700 text-slate-100 focus:border-emerald-500'
                                            : 'bg-slate-50 border-slate-200 text-slate-900 focus:border-emerald-500'
                                    }`}
                                />
                                <SecretVisibilityButton
                                    visible={Boolean(revealedSecrets.billingToken)}
                                    onToggle={() => toggleSecretVisibility('billingToken')}
                                    label="账单凭证"
                                    disabled={billingActionLoading}
                                    className="absolute right-2 top-1/2 -translate-y-1/2"
                                />
                            </div>
                            <button
                                onClick={handleQueryBilling}
                                disabled={billingLoading || billingActionLoading || !billingToken.trim()}
                                className="px-6 py-3 rounded-2xl text-sm font-bold text-white bg-emerald-600 hover:bg-emerald-500 disabled:opacity-50 transition-all shadow-md shadow-emerald-600/20 flex items-center gap-2 flex-shrink-0"
                            >
                                {billingLoading ? <RefreshCw size={16} className="animate-spin" /> : <Search size={16} />}
                                <span>查询账单状态</span>
                            </button>
                        </div>

                        {/* 账单卡片与控制 */}
                        {billingResult && (
                            <div className={`mt-6 p-6 rounded-3xl border ${
                                darkMode ? 'bg-slate-900/60 border-slate-700' : 'bg-slate-50 border-slate-200'
                            }`}>
                                <div className="flex flex-col sm:flex-row justify-between items-start sm:items-center gap-4 pb-4 border-b border-slate-700/40">
                                    <div>
                                        <div className="flex items-center gap-2">
                                            <h3 className="font-bold text-base">{billingResult.plan_name}</h3>
                                            <span className={`px-2.5 py-0.5 rounded-full text-xs font-bold ${
                                                billingResult.auto_renew
                                                    ? 'bg-emerald-500/10 text-emerald-500 border border-emerald-500/20'
                                                    : 'bg-amber-500/10 text-amber-500 border border-amber-500/20'
                                            }`}>
                                                {billingResult.auto_renew ? '已开启自动续费' : '已关闭自动续费'}
                                            </span>
                                        </div>
                                        <p className="text-xs text-slate-400 mt-1">
                                            绑定支付卡：{billingResult.card_brand} (尾号 {billingResult.card_last4}) · 下次计费日：{billingResult.next_billing_date}
                                        </p>
                                    </div>

                                    <div className="flex items-center gap-2.5">
                                        {billingResult.auto_renew ? (
                                            <button
                                                onClick={handleCancelSubscription}
                                                disabled={billingActionLoading || billingLoading}
                                                className="px-4 py-2 rounded-xl text-xs font-bold text-amber-500 border border-amber-500/30 hover:bg-amber-500/10 transition-all flex items-center gap-1.5"
                                            >
                                                <Power size={14} />
                                                <span>取消自动续费</span>
                                            </button>
                                        ) : (
                                            <button
                                                onClick={handleResumeSubscription}
                                                disabled={billingActionLoading || billingLoading}
                                                className="px-4 py-2 rounded-xl text-xs font-bold text-emerald-500 border border-emerald-500/30 hover:bg-emerald-500/10 transition-all flex items-center gap-1.5"
                                            >
                                                <RefreshCw size={14} />
                                                <span>恢复自动续费</span>
                                            </button>
                                        )}
                                    </div>
                                </div>

                                {/* 历史发票与收据 */}
                                <div className="mt-4">
                                    <h4 className="text-xs font-bold text-slate-400 mb-2">近期对账发票记录</h4>
                                    {billingResult.invoices?.length > 0 ? (
                                        <div className="space-y-2">
                                            {billingResult.invoices.map((inv, idx) => (
                                                <div
                                                    key={idx}
                                                    className={`p-3 rounded-xl border flex justify-between items-center text-xs ${
                                                        darkMode ? 'bg-slate-800 border-slate-700' : 'bg-white border-slate-200'
                                                    }`}
                                                >
                                                    <div>
                                                        <span className="font-semibold">{inv.date}</span>
                                                        <span className="text-slate-400 ml-2">金额: {inv.amount}</span>
                                                    </div>
                                                    {billingResult.is_mock && inv.slug ? <button
                                                        type="button"
                                                        onClick={() => handleBillingInvoiceDownload(inv.slug, inv.id)}
                                                        disabled={invoiceDownloading}
                                                        className="text-emerald-500 hover:underline flex items-center gap-1"
                                                    >
                                                        <Download size={13} />
                                                        <span>{invoiceDownloading ? '下载中...' : '下载收据 (TXT)'}</span>
                                                    </button> : <span className="text-xs text-slate-400">真实凭证下载暂未开放</span>}
                                                </div>
                                            ))}
                                        </div>
                                    ) : (
                                        <p className="text-xs text-slate-400">暂无发票记录</p>
                                    )}
                                </div>
                            </div>
                        )}
                    </div>
                </div>
            )}

            {/* ==================== TAB 4: 提取教程 ==================== */}
            {activeTab === 'guide' && (
                <div className="space-y-6">
                    <div className={`p-6 rounded-3xl border shadow-sm ${
                        darkMode ? 'bg-slate-800 border-slate-700 text-slate-100' : 'bg-white border-slate-200 text-slate-800'
                    }`}>
                        <h2 className="text-lg font-bold mb-4 flex items-center gap-2">
                            <HelpCircle size={20} className="text-emerald-500" />
                            <span>官方凭证提取操作指引</span>
                        </h2>

                        <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                            {/* Session 提取 */}
                            <div className={`p-5 rounded-2xl border ${darkMode ? 'bg-slate-900/60 border-slate-700' : 'bg-slate-50 border-slate-200'}`}>
                                <h3 className="font-bold text-sm text-emerald-500 mb-2">1. ChatGPT Session JSON 提取</h3>
                                <ol className="space-y-2 text-xs leading-relaxed text-slate-300 list-decimal list-inside">
                                    <li>在浏览器中登录 <a href="https://chatgpt.com" target="_blank" rel="noreferrer" className="text-emerald-400 underline">chatgpt.com</a>。</li>
                                    <li>登录完成后，在同一浏览器窗口新建标签页访问：<code className="bg-slate-800 px-1.5 py-0.5 rounded text-emerald-400">https://chatgpt.com/api/auth/session</code></li>
                                    <li>页面将返回包含 accessToken、user 与 account 对象的纯 JSON。全选并复制全部内容，粘贴到本系统的充值凭证框中。</li>
                                    <li className="text-amber-400 font-semibold">注意：提交完成前请勿点击网页「Log out 登出」，否则登录态失效将导致充值失败。</li>
                                </ol>
                            </div>

                            {/* Cookie / Claude 提取 */}
                            <div className={`p-5 rounded-2xl border ${darkMode ? 'bg-slate-900/60 border-slate-700' : 'bg-slate-50 border-slate-200'}`}>
                                <h3 className="font-bold text-sm text-teal-500 mb-2">2. Claude / Pro 5x 凭证提取</h3>
                                <ol className="space-y-2 text-xs leading-relaxed text-slate-300 list-decimal list-inside">
                                    <li>在 Chrome/Edge 扩展商店安装 <strong>Cookie-Editor</strong> 插件。</li>
                                    <li>登录目标账号后，点击右上角 Cookie-Editor 插件图标。</li>
                                    <li>点击底部 <strong>Export (导出)</strong> 并选择 <strong>Export as JSON</strong>。</li>
                                    <li>或者在 Cookies 列表中找到键名为 <code className="bg-slate-800 px-1.5 py-0.5 rounded text-teal-400">sessionKey</code> 的值，按照 <code className="bg-slate-800 px-1.5 py-0.5 rounded text-teal-400">邮箱----sk-ant-sid02-xxx</code> 格式填入即可。</li>
                                </ol>
                            </div>
                        </div>
                    </div>
                </div>
            )}

            {/* ==================== 模态弹窗：协议查看 ==================== */}
            {showAgreementModal && (
                <div className="fixed inset-0 bg-slate-900/60 backdrop-blur-sm z-50 flex items-center justify-center p-4 animate-in fade-in duration-200">
                    <div
                        role="dialog"
                        aria-modal="true"
                        aria-labelledby="recharge-agreement-title"
                        className={`rounded-3xl shadow-2xl w-full max-w-xl overflow-hidden border ${
                        darkMode ? 'bg-slate-800 border-slate-700 text-slate-100' : 'bg-white border-slate-200 text-slate-800'
                    }`}
                    >
                        <div className="p-5 border-b border-slate-700/40 flex justify-between items-center">
                            <h3 id="recharge-agreement-title" className="font-bold text-base flex items-center gap-2">
                                <FileText size={18} className="text-emerald-500" />
                                <span>充值服务协议与须知</span>
                            </h3>
                            <button onClick={() => setShowAgreementModal(false)} className="text-slate-400 hover:text-slate-200">
                                <X size={20} />
                            </button>
                        </div>
                        <div className="p-6 max-h-[65vh] overflow-y-auto">
                            <SafeAgreementContent
                                html={agreementContent}
                                className={`prose prose-sm max-w-none ${darkMode ? 'prose-invert' : ''}`}
                            />
                        </div>
                        <div className="p-4 border-t border-slate-700/40 flex justify-end">
                            <button
                                onClick={() => {
                                    setAgreeTerms(true);
                                    setShowAgreementModal(false);
                                }}
                                className="px-5 py-2 rounded-xl text-xs font-bold text-white bg-emerald-600 hover:bg-emerald-500 transition-all"
                            >
                                我已阅读并同意协议
                            </button>
                        </div>
                    </div>
                </div>
            )}

            {/* ==================== 模态弹窗：写操作二次确认 (撤回 / 关闭) ==================== */}
            {actionModal && (
                <div className="fixed inset-0 bg-slate-900/60 backdrop-blur-sm z-50 flex items-center justify-center p-4 animate-in fade-in duration-200">
                    <div
                        role="dialog"
                        aria-modal="true"
                        aria-labelledby="recharge-action-title"
                        className={`rounded-3xl shadow-2xl w-full max-w-md overflow-hidden border ${
                        darkMode ? 'bg-slate-800 border-slate-700 text-slate-100' : 'bg-white border-slate-200 text-slate-800'
                    }`}
                    >
                        <div className="p-6">
                            <div id="recharge-action-title" className="flex items-center gap-3 text-amber-500 mb-3 font-bold text-base">
                                <AlertTriangle size={24} />
                                <span>{actionModal.type === 'recall' ? '确认撤回该充值任务？' : '危险：确认关闭并销毁卡密？'}</span>
                            </div>
                            <p className="text-xs text-slate-400 leading-relaxed mb-4">
                                {actionModal.type === 'recall'
                                    ? '撤回任务后，充值流水将暂时中止，您可以修改凭证后重新发起提交。'
                                    : '关闭任务将永久注销当前 CDK 卡密并清理进行中的专卡，该操作不可撤销！'}
                            </p>
                            <div className={`mb-4 rounded-xl border px-3.5 py-2.5 text-xs ${
                                darkMode ? 'border-slate-700 bg-slate-900/60' : 'border-slate-200 bg-slate-50'
                            }`}>
                                <span className="text-slate-400">目标任务：</span>
                                <strong className="ml-1 font-mono">{actionModal.task?.task_no || '未知任务'}</strong>
                                <p className="mt-1 text-slate-400">输入的卡密必须属于该任务，否则操作会被拒绝。</p>
                            </div>

                            <div className="space-y-3 mb-5">
                                <div>
                                    <label className="block text-xs font-semibold mb-1.5">完整 CDK 卡密</label>
                                    <div className="relative">
                                        <input
                                            type={revealedSecrets.actionCdk ? 'text' : 'password'}
                                            value={actionModal.redeemCode}
                                            onChange={event => setActionModal(current => ({ ...current, redeemCode: event.target.value }))}
                                            placeholder="请输入完整卡密"
                                            maxLength={120}
                                            autoComplete="off"
                                            spellCheck={false}
                                            disabled={actionLoading}
                                            className={`w-full px-3.5 py-2.5 pr-12 rounded-xl border text-sm font-mono outline-none disabled:opacity-60 ${
                                                darkMode ? 'bg-slate-900 border-slate-700 text-slate-100' : 'bg-slate-50 border-slate-200'
                                            }`}
                                        />
                                        <SecretVisibilityButton
                                            visible={Boolean(revealedSecrets.actionCdk)}
                                            onToggle={() => toggleSecretVisibility('actionCdk')}
                                            label="操作卡密"
                                            disabled={actionLoading}
                                            className="absolute right-2 top-1/2 -translate-y-1/2"
                                        />
                                    </div>
                                </div>
                                <div>
                                    <label className="block text-xs font-semibold mb-1.5">账号邮箱</label>
                                    <input
                                        type="email"
                                        value={actionModal.email}
                                        onChange={event => setActionModal(current => ({ ...current, email: event.target.value }))}
                                        placeholder="user@gmail.com"
                                        maxLength={256}
                                        autoComplete="off"
                                        disabled={actionLoading}
                                        className={`w-full px-3.5 py-2.5 rounded-xl border text-sm outline-none disabled:opacity-60 ${
                                            darkMode ? 'bg-slate-900 border-slate-700 text-slate-100' : 'bg-slate-50 border-slate-200'
                                        }`}
                                    />
                                </div>
                            </div>

                            <div className="flex justify-end gap-3">
                                <button
                                    onClick={() => setActionModal(null)}
                                    disabled={actionLoading}
                                    className="px-4 py-2 rounded-xl text-xs font-semibold border border-slate-600 text-slate-300 hover:bg-slate-700/50"
                                >
                                    取消
                                </button>
                                <button
                                    onClick={handleConfirmTaskAction}
                                    disabled={actionLoading}
                                    className={`px-5 py-2 rounded-xl text-xs font-bold text-white ${
                                        actionModal.type === 'recall'
                                            ? 'bg-amber-600 hover:bg-amber-500'
                                            : 'bg-rose-600 hover:bg-rose-500'
                                    }`}
                                >
                                    {actionLoading ? '正在执行...' : '确认执行'}
                                </button>
                            </div>
                        </div>
                    </div>
                </div>
            )}
        </div>
    );
};

export default RechargeView;
