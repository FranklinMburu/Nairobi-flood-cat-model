import { useEffect } from "react";
import { AccessDenied } from "./components/AccessDenied";
import { AdminDashboard } from "./components/admin/AdminDashboard";
import { Login } from "./components/Login";
import { useAuth } from "./context/AuthContext";
import { ADMIN_PATH, useRouter } from "./routing";
import App from "./App";

function Redirect({ to }) {
  const { navigate } = useRouter();
  useEffect(() => {
    navigate(to, { replace: true });
  }, [navigate, to]);
  return <div className="boot"> </div>;
}

export function Root() {
  const { user, loading } = useAuth();
  const { path } = useRouter();

  useEffect(() => {
    if (path === "/login") document.title = "Sign in · DIRA";
    else if (path === ADMIN_PATH) document.title = "Administration · DIRA";
    else document.title = "CAT Intelligence · Nairobi";
  }, [path]);

  if (loading) return <div className="boot">Checking your session…</div>;
  if (!user) {
    if (path === "/login") return <Login />;
    return <Redirect to="/login" />;
  }
  if (path === "/login") return <Redirect to="/dashboard" />;
  if (path === ADMIN_PATH) {
    if (!user.is_staff) return <AccessDenied />;
    return <AdminDashboard />;
  }
  if (path !== "/dashboard") return <Redirect to="/dashboard" />;
  return <App />;
}
