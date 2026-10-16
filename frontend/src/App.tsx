import { type ReactNode, useEffect } from "react";
import { Link, Navigate, Route, Routes, useLocation } from "react-router";

import { LoadingState } from "./components/LoadingState";
import { Toaster } from "./components/Toaster";
import { AppLayout } from "./layouts/AppLayout";
import { ActivityPage } from "./pages/ActivityPage";
import { DashboardPage } from "./pages/DashboardPage";
import { DeploymentPage } from "./pages/DeploymentPage";
import { LoginPage } from "./pages/LoginPage";
import { NewProjectPage } from "./pages/NewProjectPage";
import { DeploymentsTab } from "./pages/project/DeploymentsTab";
import { EnvironmentTab } from "./pages/project/EnvironmentTab";
import { LogsTab } from "./pages/project/LogsTab";
import { MetricsTab } from "./pages/project/MetricsTab";
import { OverviewTab } from "./pages/project/OverviewTab";
import { ProjectLayout } from "./pages/project/ProjectLayout";
import { SettingsTab } from "./pages/project/SettingsTab";
import { RegisterPage } from "./pages/RegisterPage";
import { useAuth } from "./stores/auth";
import { useDeployments } from "./stores/deployments";
import { useProjects } from "./stores/projects";

function RequireAuth({ children }: { children: ReactNode }) {
  const status = useAuth((s) => s.status);
  const location = useLocation();
  if (status === "loading") return <LoadingState label="Restoring session…" />;
  if (status === "anonymous") return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  return children;
}

function GuestOnly({ children }: { children: ReactNode }) {
  const status = useAuth((s) => s.status);
  if (status === "loading") return <LoadingState label="Restoring session…" />;
  if (status === "authenticated") return <Navigate to="/" replace />;
  return children;
}

function NotFound() {
  return (
    <div className="flex flex-col items-center gap-3 py-20 text-center">
      <p className="font-mono text-5xl font-semibold text-subtle">404</p>
      <p className="text-muted">This page does not exist.</p>
      <Link to="/" className="text-accent hover:underline">
        Back to the dashboard
      </Link>
    </div>
  );
}

export function App() {
  const bootstrap = useAuth((s) => s.bootstrap);
  const status = useAuth((s) => s.status);

  useEffect(() => {
    void bootstrap();
  }, [bootstrap]);

  // Never keep another user's data around after logout.
  useEffect(() => {
    if (status === "anonymous") {
      useProjects.getState().reset();
      useDeployments.getState().reset();
    }
  }, [status]);

  return (
    <>
      <Routes>
        <Route
          path="/login"
          element={
            <GuestOnly>
              <LoginPage />
            </GuestOnly>
          }
        />
        <Route
          path="/register"
          element={
            <GuestOnly>
              <RegisterPage />
            </GuestOnly>
          }
        />
        <Route
          element={
            <RequireAuth>
              <AppLayout />
            </RequireAuth>
          }
        >
          <Route index element={<DashboardPage />} />
          <Route path="projects/new" element={<NewProjectPage />} />
          <Route path="projects/:projectId" element={<ProjectLayout />}>
            <Route index element={<OverviewTab />} />
            <Route path="deployments" element={<DeploymentsTab />} />
            <Route path="logs" element={<LogsTab />} />
            <Route path="environment" element={<EnvironmentTab />} />
            <Route path="metrics" element={<MetricsTab />} />
            <Route path="settings" element={<SettingsTab />} />
          </Route>
          <Route path="deployments/:deploymentId" element={<DeploymentPage />} />
          <Route path="activity" element={<ActivityPage />} />
          <Route path="*" element={<NotFound />} />
        </Route>
      </Routes>
      <Toaster />
    </>
  );
}
