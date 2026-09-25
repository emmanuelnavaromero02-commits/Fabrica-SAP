import { useEffect } from "react";

import { useStored } from "../../hooks";

type Theme = "system" | "light" | "dark";

const NEXT: Record<Theme, Theme> = { system: "light", light: "dark", dark: "system" };
const LABEL: Record<Theme, string> = { system: "🖥️ Sistema", light: "☀️ Claro", dark: "🌙 Oscuro" };

export function ThemeToggle() {
  const [theme, setTheme] = useStored<Theme>("fabrica.theme", "system");
  useEffect(() => {
    const root = document.documentElement;
    if (theme === "system") root.removeAttribute("data-theme");
    else root.setAttribute("data-theme", theme);
  }, [theme]);
  return (
    <button className="ghost" onClick={() => setTheme(NEXT[theme])} title="Cambiar tema">
      {LABEL[theme]}
    </button>
  );
}
