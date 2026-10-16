import { Menu, Plus } from "lucide-react";
import { Link } from "react-router";

import { Logo } from "./ProjectSidebar";

/** Compact top bar for small screens; the sidebar opens as a drawer. */
export function Navbar({ onMenu }: { onMenu: () => void }) {
  return (
    <header className="sticky top-0 z-30 flex h-14 items-center justify-between gap-3 border-b border-line bg-canvas/90 px-4 backdrop-blur lg:hidden">
      <button
        onClick={onMenu}
        className="-ml-2 inline-flex h-10 w-10 items-center justify-center rounded-lg text-muted hover:bg-raised hover:text-fg"
        aria-label="Open navigation"
      >
        <Menu className="h-5 w-5" />
      </button>
      <Link to="/" aria-label="Dashboard">
        <Logo />
      </Link>
      <Link
        to="/projects/new"
        className="-mr-2 inline-flex h-10 w-10 items-center justify-center rounded-lg text-muted hover:bg-raised hover:text-fg"
        aria-label="New project"
      >
        <Plus className="h-5 w-5" />
      </Link>
    </header>
  );
}
