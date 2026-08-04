// SPDX-License-Identifier: AGPL-3.0-or-later
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { BASE_PATH } from './basePath';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { AdminRoute, AuthProvider, ProtectedRoute } from './auth';
import { DarkModeProvider } from './hooks';
import { AppShell } from './components/layout/AppShell';
import LoginPage from './pages/LoginPage';
import DashboardPage from './pages/DashboardPage';
import RulesPage from './pages/RulesPage';
import { HelpPage } from './help/HelpPage';
import ExplorerPage from './pages/ExplorerPage';
import AssetDetailsPage from './pages/AssetDetailsPage';
import AssetHistoryPage from './pages/AssetHistoryPage';
import AlertsPage from './pages/AlertsPage';
import ReportingPage from './pages/reporting/ReportingPage';
import ReportDetailPage from './pages/reporting/ReportDetailPage';
import IntegrationsPage from './pages/IntegrationsPage';
import IntegrationDetailsPage from './pages/IntegrationDetailsPage';
import IntegrationWizardPage from './pages/IntegrationWizardPage';
import SettingsPage from './pages/SettingsPage';
import { CategorySettings } from './pages/settings/CategorySettings';
import { AboutTab } from './pages/settings/AboutTab';
import { AuditLogTab } from './pages/settings/AuditLogTab';
import { ChatbotSettingsTab } from './pages/settings/ChatbotSettingsTab';
import { ChatBehaviorSettingsTab } from './pages/settings/ChatBehaviorSettingsTab';
import { ChatbotUsageTab } from './pages/settings/ChatbotUsageTab';
import { ChatbotTeamsTab } from './pages/settings/ChatbotTeamsTab';
import { EmailSettingsTab } from './pages/settings/EmailSettingsTab';
import { LdapSettingsTab } from './pages/settings/LdapSettingsTab';
import { OidcSettingsTab } from './pages/settings/OidcSettingsTab';
import { SecuritySettingsTab } from './pages/settings/SecuritySettingsTab';
import { FieldRegistryTab } from './pages/settings/FieldRegistryTab';
import { EnrichmentTab } from './pages/settings/EnrichmentTab';
import { UnifiedFieldsTab } from './pages/settings/UnifiedFieldsTab';
import { ReferenceDataTab } from './pages/settings/ReferenceDataTab';
import { MergeDenylistTab } from './pages/settings/MergeDenylistTab';
import { AssetGenerationTab } from './pages/settings/AssetGenerationTab';
import { ConfidenceTab } from './pages/settings/ConfidenceTab';
import { UsersTab } from './pages/settings/UsersTab';
import { ApiKeysTab } from './pages/settings/ApiKeysTab';
import { DataGroupPage } from './pages/settings/DataGroupPage';
import { SystemGroupPage } from './pages/settings/SystemGroupPage';
import { AccessGroupPage } from './pages/settings/AccessGroupPage';
import { ChatbotGroupPage } from './pages/settings/ChatbotGroupPage';
import { AlertDeliveryGroupPage } from './pages/settings/AlertDeliveryGroupPage';
import { AlertChannelsTab } from './pages/settings/AlertChannelsTab';
import { AlertRoutingTab } from './pages/settings/AlertRoutingTab';
import { MonitoringGroupPage } from './pages/settings/MonitoringGroupPage';
import { MonitoringSettingsTab } from './pages/settings/MonitoringSettingsTab';
import OidcCompletePage from './pages/OidcCompletePage';
import MfaEnrollmentRequiredPage from './pages/MfaEnrollmentRequiredPage';
import PasswordResetRequestPage from './pages/PasswordResetRequestPage';
import PasswordResetVerifyPage from './pages/PasswordResetVerifyPage';
import OnboardingPage from './pages/OnboardingPage';
import AccountPage from './pages/account/AccountPage';
import DataSourceDetailsPage from './pages/datasource/DataSourceDetailsPage';
import MonitoringPage from './pages/monitoring/MonitoringPage';


const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      retry: 1,
    },
  },
});

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <AuthProvider>
        <DarkModeProvider>
          <BrowserRouter basename={BASE_PATH || undefined}>
              <Routes>
                {/* Public routes */}
                <Route path="/login" element={<LoginPage />} />
                <Route path="/oidc-complete" element={<OidcCompletePage />} />
                <Route
                  path="/mfa-enrollment-required"
                  element={<MfaEnrollmentRequiredPage />}
                />
                <Route path="/password-reset" element={<PasswordResetRequestPage />} />
                <Route path="/password-reset/verify" element={<PasswordResetVerifyPage />} />
                <Route path="/onboarding" element={<OnboardingPage />} />

                {/* Protected routes */}
                <Route element={<ProtectedRoute />}>
                  <Route element={<AppShell />}>
                    <Route path="/dashboard" element={<DashboardPage />} />
                    <Route path="/explorer" element={<ExplorerPage />} />
                    <Route path="/reporting" element={<ReportingPage />} />
                    <Route path="/reporting/new" element={<ReportDetailPage />} />
                    <Route path="/reporting/:id" element={<ReportDetailPage />} />
                    <Route path="/alerts" element={<AlertsPage />} />
                    <Route path="/rules" element={<RulesPage />} />
                    <Route path="/help" element={<HelpPage />} />
                    <Route path="/help/:topicId" element={<HelpPage />} />
                    <Route path="/asset-tags" element={<Navigate to="/rules" replace />} />
                    <Route path="/monitoring" element={<Navigate to="/settings/monitoring/dashboards" replace />} />
                    <Route path="/settings" element={<SettingsPage />}>
                      <Route index element={<Navigate to="system" replace />} />

                      {/* Data Pipeline */}
                      <Route path="data" element={<DataGroupPage />}>
                        <Route index element={<Navigate to="pipeline" replace />} />
                        <Route path="pipeline" element={<AssetGenerationTab />} />
                        <Route path="confidence" element={<ConfidenceTab />} />
                        <Route path="semantic" element={<FieldRegistryTab />} />
                        {/* `fields` was the old route name — kept as a
                           redirect so existing bookmarks still land on
                           the renamed Semantic tab. */}
                        <Route path="fields" element={<Navigate to="/settings/data/semantic" replace />} />
                        <Route path="enrichment" element={<EnrichmentTab />} />
                        <Route path="unified-fields" element={<UnifiedFieldsTab />} />
                        <Route path="reference-data" element={<ReferenceDataTab />} />
                        <Route path="merge-denylist" element={<MergeDenylistTab />} />
                      </Route>

                      {/* System */}
                      <Route path="system" element={<SystemGroupPage />}>
                        <Route index element={<Navigate to="retention" replace />} />
                        <Route path="retention" element={<CategorySettings category="retention" />} />
                        <Route path="security" element={<SecuritySettingsTab />} />
                        <Route path="logging" element={<CategorySettings category="logging" />} />
                        <Route path="email" element={<EmailSettingsTab />} />
                        <Route path="about" element={<AboutTab />} />
                      </Route>

                      {/* Posture moved to the Rules page (under AssetAtlas Rules). */}
                      <Route path="posture" element={<Navigate to="/rules" replace />} />

                      {/* Access (Users + API Keys + LDAP + OIDC + Audit Log) */}
                      <Route path="access" element={<AccessGroupPage />}>
                        <Route index element={<Navigate to="users" replace />} />
                        <Route path="users" element={<UsersTab />} />
                        <Route path="api-keys" element={<ApiKeysTab />} />
                        <Route path="ldap" element={<LdapSettingsTab />} />
                        <Route path="oidc" element={<OidcSettingsTab />} />
                        <Route path="audit" element={<AuditLogTab />} />
                      </Route>

                      {/* Chatbot */}
                      <Route path="chatbot" element={<ChatbotGroupPage />}>
                        <Route index element={<Navigate to="configuration" replace />} />
                        <Route path="configuration" element={<ChatbotSettingsTab />} />
                        <Route path="behavior" element={<ChatBehaviorSettingsTab />} />
                        <Route path="usage" element={<ChatbotUsageTab />} />
                        <Route path="teams" element={<ChatbotTeamsTab />} />
                      </Route>

                      {/* Alert Delivery (Channels + Routing) */}
                      <Route path="alert-delivery" element={<AlertDeliveryGroupPage />}>
                        <Route index element={<Navigate to="channels" replace />} />
                        <Route path="channels" element={<AlertChannelsTab />} />
                        <Route path="routing" element={<AlertRoutingTab />} />
                      </Route>

                      {/* Monitoring (Forwarding config #922 + Dashboards viewer).
                         The dashboards viewer previously sat alone at
                         /settings/monitoring; it's now the group's second sub-tab
                         and the group defaults to the forwarding config. */}
                      <Route path="monitoring" element={<MonitoringGroupPage />}>
                        <Route index element={<Navigate to="forwarding" replace />} />
                        <Route path="forwarding" element={<MonitoringSettingsTab />} />
                        <Route path="dashboards" element={<MonitoringPage />} />
                      </Route>
                      {/* About moved under /settings/system/about; keep this path
                         as a redirect for old bookmarks. */}
                      <Route path="about" element={<Navigate to="/settings/system/about" replace />} />

                      {/* Backward-compat redirects from old /settings/<leaf> paths
                         and from the previous Data/Users/System groupings. The
                         `chatbot` segment isn't redirected — it still lives at its
                         own group route, and the index Navigate forwards a bare
                         `/settings/chatbot` to its first sub-tab. */}
                      <Route path="asset-generation" element={<Navigate to="/settings/data/pipeline" replace />} />
                      <Route path="posture-rules" element={<Navigate to="/rules" replace />} />
                      <Route path="field-registry" element={<Navigate to="/settings/data/semantic" replace />} />
                      <Route path="data-retention" element={<Navigate to="/settings/system/retention" replace />} />
                      <Route path="security" element={<Navigate to="/settings/system/security" replace />} />
                      <Route path="logging" element={<Navigate to="/settings/system/logging" replace />} />
                      <Route path="enrichment" element={<Navigate to="/settings/data/enrichment" replace />} />
                      <Route path="email" element={<Navigate to="/settings/system/email" replace />} />
                      <Route path="api-keys" element={<Navigate to="/settings/access/api-keys" replace />} />
                      <Route path="audit-log" element={<Navigate to="/settings/access/audit" replace />} />
                      <Route path="chatbot-usage" element={<Navigate to="/settings/chatbot/usage" replace />} />
                      {/* Posture moved out of Settings entirely → Rules page */}
                      <Route path="data/posture" element={<Navigate to="/rules" replace />} />
                      {/* Users group retired — its sub-tabs live under /settings/access/* now */}
                      <Route path="users" element={<Navigate to="/settings/access/users" replace />} />
                      <Route path="users/list" element={<Navigate to="/settings/access/users" replace />} />
                      <Route path="users/api-keys" element={<Navigate to="/settings/access/api-keys" replace />} />
                      <Route path="users/audit" element={<Navigate to="/settings/access/audit" replace />} />
                      {/* LDAP / OIDC moved from System into Access */}
                      <Route path="system/ldap" element={<Navigate to="/settings/access/ldap" replace />} />
                      <Route path="system/oidc" element={<Navigate to="/settings/access/oidc" replace />} />
                    </Route>
                    <Route path="/pipeline" element={<Navigate to="/settings/data/pipeline" replace />} />
                    <Route path="/settings/alert-rules" element={<Navigate to="/rules" replace />} />
                    <Route path="/audit-log" element={<Navigate to="/settings/access/audit" replace />} />
                    <Route path="/asset-details" element={<AssetDetailsPage />} />
                    <Route path="/asset-history" element={<AssetHistoryPage />} />
                    {/* Admin-only. Every /api/integrations route requires admin,
                        so a standard user reaching these by URL would land on a
                        page whose every request 403s. The sidebar mutes the
                        link; this closes the URL behind it. */}
                    <Route element={<AdminRoute />}>
                      <Route path="/integrations" element={<IntegrationsPage />} />
                      <Route path="/integration-details" element={<IntegrationDetailsPage />} />
                      <Route path="/integration-wizard" element={<IntegrationWizardPage />} />
                    </Route>
                    <Route path="/datasource-details" element={<DataSourceDetailsPage />} />
                    <Route path="/account" element={<AccountPage />} />
                    <Route path="/profile" element={<Navigate to="/account" replace />} />
                  </Route>
                </Route>

                {/* Root → login */}
                <Route path="/" element={<Navigate to="/login" replace />} />
              </Routes>
          </BrowserRouter>
        </DarkModeProvider>
      </AuthProvider>
    </QueryClientProvider>
  );
}
