import { useOutletContext } from "react-router";

import type { Project } from "../../types/api";

/** The project loaded by ProjectLayout, available to every tab. */
export function useProject(): Project {
  return useOutletContext<{ project: Project }>().project;
}
