// SPDX-License-Identifier: AGPL-3.0-or-later
import { Navigate, Outlet } from 'react-router-dom';
import { useAuth } from './useAuth';

export function AdminRoute() {
  const { isAdmin, isLoading } = useAuth();

  if (isLoading) {
    return (
      <div
        style={{
          display: 'flex',
          justifyContent: 'center',
          alignItems: 'center',
          height: '100vh',
        }}
      >
        <div className="loading-spinner" />
      </div>
    );
  }

  if (!isAdmin) {
    return <Navigate to="/dashboard" replace />;
  }

  return <Outlet />;
}
