import { CheckCircle2, Info, X, XCircle } from "lucide-react";

import { type ToastKind, useToasts } from "../stores/toast";

const ICONS: Record<ToastKind, typeof Info> = { success: CheckCircle2, error: XCircle, info: Info };
const COLORS: Record<ToastKind, string> = { success: "text-ok", error: "text-bad", info: "text-info" };

export function Toaster() {
  const toasts = useToasts((s) => s.toasts);
  const dismiss = useToasts((s) => s.dismiss);
  return (
    <div
      aria-live="polite"
      className="pointer-events-none fixed inset-x-3 bottom-3 z-[60] flex flex-col gap-2 sm:inset-x-auto sm:right-4 sm:bottom-4 sm:w-96"
    >
      {toasts.map((t) => {
        const Icon = ICONS[t.kind];
        return (
          <div
            key={t.id}
            role={t.kind === "error" ? "alert" : "status"}
            className="animate-toast-in pointer-events-auto flex items-start gap-3 rounded-xl border border-line bg-raised p-3.5 shadow-xl"
          >
            <Icon className={`mt-0.5 h-5 w-5 shrink-0 ${COLORS[t.kind]}`} aria-hidden />
            <div className="min-w-0 flex-1">
              <p className="text-sm font-medium">{t.title}</p>
              {t.description && <p className="mt-0.5 break-words text-xs text-muted">{t.description}</p>}
            </div>
            <button onClick={() => dismiss(t.id)} className="text-subtle hover:text-fg" aria-label="Dismiss">
              <X className="h-4 w-4" />
            </button>
          </div>
        );
      })}
    </div>
  );
}
