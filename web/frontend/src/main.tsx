import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./app";
import { bootTg } from "./theme";
import "./styles.css";

// Theme + SDK boot before first paint (v1 called bootTg() on load).
bootTg();

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
