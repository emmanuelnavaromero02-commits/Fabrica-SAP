import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { App } from "./App";
import "./styles/tokens.css";
import "./styles/layout.css";
import "./styles/components.css";

try {
  const theme = JSON.parse(localStorage.getItem("fabrica.theme") ?? '"system"') as string;
  if (theme === "light" || theme === "dark") document.documentElement.setAttribute("data-theme", theme);
} catch {
  document.documentElement.removeAttribute("data-theme");
}

const root = document.getElementById("root");
if (!root) throw new Error("No se encontró #root");

createRoot(root).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
