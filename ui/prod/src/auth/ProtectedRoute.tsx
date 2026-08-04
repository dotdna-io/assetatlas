// SPDX-License-Identifier: AGPL-3.0-or-later
import React from 'react';
import { Navigate, Outlet, useLocation } from 'react-router-dom';
import { useAuth } from './useAuth';
import { setIntendedPath } from './auth-service';

export function ProtectedRoute() {
  const { isAuthenticated, isLoading, user } = useAuth();
  const location = useLocation();

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

  if (!isAuthenticated) {
    setIntendedPath(location.pathname + location.search);
    return <Navigate to="/login" replace />;
  }

  // Password expired — force the user onto /account until they change it.
  // Allow /account itself so the page can render its forced-change banner.
  if (user?.must_change_password && location.pathname !== '/account') {
    return <Navigate to="/account?force=password" replace />;
  }

  return <Outlet />;
}
