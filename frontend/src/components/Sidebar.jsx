import { ADMIN_PATH, useRouter } from "../routing";

const ITEMS = [
  ["overview", "Overview"],
  ["map", "Risk Map"],
  ["exposure", "Exposure"],
  ["loss", "Loss Analysis"],
  ["reports", "Reports"],
  ["submissions", "Submissions"],
  ["upload", "File upload"],
  ["runs", "Model runs"],
];

export function Sidebar({ page, onNavigate, user, onSignOut, signingOut, signOutError, scenario }) {
  const { navigate } = useRouter();
  return (
    <aside className="sidebar">
      <div className="brand">
        <div className="brand-mark">Kenya Re</div>
        <div className="brand-name">CAT Intelligence</div>
      </div>
      <nav>
        {ITEMS.map(([id, label]) => (
          <button
            key={id}
            type="button"
            className={page === id ? "nav on" : "nav"}
            onClick={() => onNavigate(id)}
          >
            {label}
          </button>
        ))}
      </nav>
      <div className="sidebar-bottom">
        <div className="sidebar-user">
          <strong>{user.name}</strong>
          <span title={user.email}>{user.email}</span>
          {user.is_staff ? (
            <button type="button" className="sidebar-admin" onClick={() => navigate(ADMIN_PATH)}>
              Administration
            </button>
          ) : null}
          <button type="button" onClick={onSignOut} disabled={signingOut}>
            {signingOut ? "Signing out..." : "Sign out"}
          </button>
          {signOutError ? <em role="alert">{signOutError}</em> : null}
        </div>
        <div className="sidebar-foot">
          <span>Damage assumption</span>
          <strong>{scenario ? `${scenario.short_label} (H = ${scenario.h})` : "—"}</strong>
          <span>Synthetic portfolio</span>
        </div>
      </div>
    </aside>
  );
}
