import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

// Placeholder entry point: App and the review components land in Task 2 (Spec 05 plan).
const container = document.getElementById("root");
if (!container) {
  throw new Error("root element not found");
}

createRoot(container).render(
  <StrictMode>
    <p>Pitz Pulse review UI — components land in the next task.</p>
  </StrictMode>,
);
