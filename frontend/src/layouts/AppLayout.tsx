import { useEffect, useState } from "react";
import { Outlet } from "react-router";

import { Navbar } from "../components/Navbar";
import { ProjectSidebar } from "../components/ProjectSidebar";
import { useEvents } from "../stores/events";
import { useProjects } from "../stores/projects";

export function AppLayout() {
  const [drawer, setDrawer] = useState(false);
  const connect = useEvents((s) => s.connect);
  const disconnect = useEvents((s) => s.disconnect);
  const fetchAll = useProjects((s) => s.fetchAll);

  // Live events for the whole session; project list feeds the sidebar.
  useEffect(() => {
    connect();
    void fetchAll();
    return disconnect;
  }, [connect, disconnect, fetchAll]);

  return (
    <div className="min-h-screen lg:grid lg:grid-cols-[16rem_1fr]">
      <aside className="sticky top-0 hidden h-screen border-r border-line bg-surface lg:block">
        <ProjectSidebar />
      </aside>

      {drawer && (
        <div className="fixed inset-0 z-40 lg:hidden" role="dialog" aria-modal="true" aria-label="Navigation">
          <div className="absolute inset-0 bg-black/60" onClick={() => setDrawer(false)} aria-hidden />
          <aside className="absolute inset-y-0 left-0 w-72 max-w-[85vw] border-r border-line bg-surface shadow-2xl">
            <ProjectSidebar onNavigate={() => setDrawer(false)} />
          </aside>
        </div>
      )}

      <div className="flex min-w-0 flex-col">
        <Navbar onMenu={() => setDrawer(true)} />
        <main className="mx-auto w-full max-w-6xl min-w-0 px-4 py-6 sm:px-6 lg:px-8 lg:py-8">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
