import { createContext, type ReactNode, useCallback, useContext, useState } from "react";

interface Toast {
  id: number;
  text: string;
  kind: "info" | "error";
}

type Notify = (text: string, kind?: Toast["kind"]) => void;

const ToastContext = createContext<Notify>(() => undefined);

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const notify = useCallback<Notify>((text, kind = "info") => {
    const id = Date.now() + Math.random();
    setToasts((current) => [...current, { id, text, kind }]);
    setTimeout(() => setToasts((current) => current.filter((t) => t.id !== id)), 4500);
  }, []);
  return (
    <ToastContext.Provider value={notify}>
      {children}
      <div className="toasts" role="status" aria-live="polite">
        {toasts.map((t) => (
          <div key={t.id} className={`toast ${t.kind}`}>
            {t.text}
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}

export const useToast = () => useContext(ToastContext);

export function errorText(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}
