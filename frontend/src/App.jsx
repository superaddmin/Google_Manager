import React, { lazy, Suspense } from 'react';
import RechargePortal from './components/RechargePortal';

const AdminApp = lazy(() => import('./AdminApp'));

const App = () => {
    const isAdminPortal = window.location.pathname === '/admin' || window.location.pathname.startsWith('/admin/');

    if (!isAdminPortal) {
        return <RechargePortal />;
    }

    return (
        <Suspense fallback={<div className="min-h-screen bg-slate-950 text-slate-200 flex items-center justify-center">正在加载管理后台...</div>}>
            <AdminApp />
        </Suspense>
    );
};

export default App;
