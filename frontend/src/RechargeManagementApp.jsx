import React, { lazy, Suspense } from 'react';

const CdkAdminApp = lazy(() => import('./CdkAdminApp'));
const RechargeAdminApp = lazy(() => import('./RechargeAdminApp'));

export default function RechargeManagementApp() {
    const orders = new URLSearchParams(window.location.search).get('tab') === 'orders';
    const linkClass = active => `rounded-lg px-4 py-2 text-sm ${active ? 'bg-emerald-700 text-white' : 'bg-white text-slate-700 border border-slate-300 hover:bg-slate-100'}`;

    return <div className="min-h-screen bg-slate-50 text-slate-900">
        <header className="bg-white border-b border-slate-200">
            <div className="max-w-[1600px] mx-auto p-4 sm:px-6 space-y-4">
                <div className="flex justify-between items-center flex-wrap gap-3">
                    <div><h1 className="font-bold text-xl">Chat GPT充值中心 · 管理后台</h1><p className="text-sm text-slate-500 mt-1">生成和管理 CDK 卡密，查看充值订单</p></div>
                    <div className="flex gap-4 text-sm text-emerald-700"><a href="/recharge">用户充值页</a><a href="/Googlemail">邮箱管理</a></div>
                </div>
                <nav aria-label="充值后台模块" className="flex flex-wrap gap-3">
                    <a className={linkClass(!orders)} href="/admin" aria-current={!orders ? 'page' : undefined}>CDK 卡密管理</a>
                    <a className={linkClass(orders)} href="/admin?tab=orders" aria-current={orders ? 'page' : undefined}>原充值订单</a>
                </nav>
            </div>
        </header>
        <Suspense fallback={<p className="p-6">正在加载管理模块…</p>}>
            {orders ? <RechargeAdminApp embedded /> : <CdkAdminApp embedded />}
        </Suspense>
    </div>;
}
