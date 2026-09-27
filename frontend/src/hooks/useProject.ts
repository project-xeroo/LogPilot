import { useSession } from "@/store/session";

/** The active project's id. Screens under AppShell can rely on it being set. */
export function useProjectId(): string {
  return useSession((s) => s.projectId) ?? "";
}

export function useProject() {
  const user = useSession((s) => s.user);
  const id = useSession((s) => s.projectId);
  return user?.projects.find((p) => p.id === id) ?? null;
}
