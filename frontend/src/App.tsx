import { lazy, Suspense } from "react";
import { Navigate, Route, Routes, useLocation } from "react-router-dom";
import { AppShell } from "@/components/shared/AppShell";
import { Spinner } from "@/components/shared/ui";
import { can, useSession } from "@/store/session";

const LoginPage = lazy(() => import("@/pages/LoginPage"));
const HomePage = lazy(() => import("@/pages/HomePage"));
const ChatPage = lazy(() => import("@/pages/ChatPage"));
const RiskBoardPage = lazy(() => import("@/pages/RiskBoardPage"));
const AlertsPage = lazy(() => import("@/pages/AlertsPage"));
const SearchPage = lazy(() => import("@/pages/SearchPage"));
const ReportsPage = lazy(() => import("@/pages/ReportsPage"));
const DeploymentsPage = lazy(() => import("@/pages/DeploymentsPage"));
const SettingsPolicyPage = lazy(() => import("@/pages/SettingsPolicyPage"));
const SettingsRolesPage = lazy(() => import("@/pages/SettingsRolesPage"));
const SettingsApiKeysPage = lazy(() => import("@/pages/SettingsApiKeysPage"));

function RequireAuth({ children }: { children: React.ReactNode }) {
  const token = useSession((s) => s.token);
  const loc = useLocation();
  return token ? <>{children}</> : <Navigate to="/login" replace state={{ from: loc.pathname + loc.search }} />;
}

/** Route guard by permission: a person who can't use a screen is sent home instead of seeing a broken page. */
function Guard({ perm, children }: { perm: string; children: React.ReactNode }) {
  const user = useSession((s) => s.user);
  return can(user, perm) ? <>{children}</> : <Navigate to="/" replace />;
}

export default function App() {
  return (
    <Suspense fallback={<div className="grid min-h-screen place-items-center"><Spinner label="Loading" /></div>}>
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route element={<RequireAuth><AppShell /></RequireAuth>}>
          <Route index element={<HomePage />} />
          <Route path="chat" element={<Guard perm="chat.use"><ChatPage /></Guard>} />
          <Route path="risk" element={<Guard perm="risk.view"><RiskBoardPage /></Guard>} />
          <Route path="alerts" element={<Guard perm="alerts.view"><AlertsPage /></Guard>} />
          <Route path="search" element={<Guard perm="logs.search"><SearchPage /></Guard>} />
          <Route path="reports" element={<Guard perm="reports.view"><ReportsPage /></Guard>} />
          <Route path="deployments" element={<Guard perm="deployments.view"><DeploymentsPage /></Guard>} />
          <Route path="settings/policy" element={<Guard perm="policy.view"><SettingsPolicyPage /></Guard>} />
          <Route path="settings/roles" element={<Guard perm="users.promote"><SettingsRolesPage /></Guard>} />
          <Route path="settings/api-keys" element={<Guard perm="api_keys.manage"><SettingsApiKeysPage /></Guard>} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Route>
      </Routes>
    </Suspense>
  );
}
