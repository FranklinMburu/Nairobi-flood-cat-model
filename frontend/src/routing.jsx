import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";

const RouteContext = createContext(null);

/** Staff account screen. Not a security boundary; the API still requires a staff session. */
export const ADMIN_PATH = "/dira-steward";

function normalize(pathname) {
  if (!pathname || pathname === "/") return "/";
  const trimmed = pathname.replace(/\/+$/, "");
  return trimmed || "/";
}

export function Router({ children }) {
  const [path, setPath] = useState(() => normalize(window.location.pathname));

  useEffect(() => {
    const sync = () => setPath(normalize(window.location.pathname));
    window.addEventListener("popstate", sync);
    return () => window.removeEventListener("popstate", sync);
  }, []);

  const navigate = useCallback((to, { replace = false } = {}) => {
    const next = normalize(to);
    if (next !== normalize(window.location.pathname)) {
      const method = replace ? "replaceState" : "pushState";
      window.history[method]({}, "", next);
    }
    setPath(next);
  }, []);

  const value = useMemo(() => ({ path, navigate }), [path, navigate]);
  return <RouteContext.Provider value={value}>{children}</RouteContext.Provider>;
}

export function useRouter() {
  const value = useContext(RouteContext);
  if (!value) throw new Error("useRouter must be used within Router.");
  return value;
}
