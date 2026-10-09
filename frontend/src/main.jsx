import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { AuthProvider } from "./context/AuthContext";
import { Root } from "./Root";
import { Router } from "./routing";
import "./styles.css";

createRoot(document.getElementById("root")).render(
  <StrictMode>
    <AuthProvider>
      <Router>
        <Root />
      </Router>
    </AuthProvider>
  </StrictMode>,
);
