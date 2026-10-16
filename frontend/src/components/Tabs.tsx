import { NavLink } from "react-router";

export interface TabItem {
  to: string;
  label: string;
  end?: boolean;
}

/** Route-driven tabs; scrolls horizontally on narrow screens instead of wrapping. */
export function Tabs({ items }: { items: TabItem[] }) {
  return (
    <nav
      className="-mx-4 overflow-x-auto border-b border-line px-4 sm:mx-0 sm:px-0"
      aria-label="Project sections"
    >
      <ul className="flex min-w-max gap-1">
        {items.map((item) => (
          <li key={item.to}>
            <NavLink
              to={item.to}
              end={item.end}
              className={({ isActive }) =>
                `-mb-px inline-flex h-10 items-center border-b-2 px-3 text-sm transition-colors ${
                  isActive ? "border-accent text-fg" : "border-transparent text-muted hover:text-fg"
                }`
              }
            >
              {item.label}
            </NavLink>
          </li>
        ))}
      </ul>
    </nav>
  );
}
