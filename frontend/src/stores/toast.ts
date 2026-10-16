import { create } from "zustand";

export type ToastKind = "success" | "error" | "info";

export interface Toast {
  id: number;
  kind: ToastKind;
  title: string;
  description?: string;
}

interface ToastState {
  toasts: Toast[];
  push: (kind: ToastKind, title: string, description?: string) => void;
  dismiss: (id: number) => void;
}

let nextId = 1;

export const useToasts = create<ToastState>((set, get) => ({
  toasts: [],
  push: (kind, title, description) => {
    const id = nextId++;
    set({ toasts: [...get().toasts.slice(-4), { id, kind, title, description }] });
    setTimeout(() => get().dismiss(id), kind === "error" ? 7000 : 4000);
  },
  dismiss: (id) => set({ toasts: get().toasts.filter((t) => t.id !== id) }),
}));

export const toast = {
  success: (title: string, description?: string) => useToasts.getState().push("success", title, description),
  error: (title: string, description?: string) => useToasts.getState().push("error", title, description),
  info: (title: string, description?: string) => useToasts.getState().push("info", title, description),
};
