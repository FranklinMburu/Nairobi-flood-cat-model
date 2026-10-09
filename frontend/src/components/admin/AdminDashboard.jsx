import { useEffect, useState } from "react";
import { useAuth } from "../../context/AuthContext";
import {
  createAdminUser,
  getAdminStats,
  getAdminUsers,
  getAuditLog,
  getSecuritySettings,
  saveSecuritySettings,
  setUserActive,
} from "../../services/api";
import { useRouter } from "../../routing";

const SECTIONS = [
  ["overview", "Overview"],
  ["users", "Users"],
  ["security", "Security"],
  ["audit", "Audit log"],
];

const ACTION_LABELS = {
  user_created: "Created user",
  user_deactivated: "Deactivated user",
  user_reactivated: "Reactivated user",
  login_succeeded: "Signed in",
  login_failed: "Failed sign-in",
  password_accepted: "Password accepted",
  otp_requested: "Verification requested",
  otp_sent: "Verification code sent",
  otp_resent: "Verification code resent",
  otp_verified: "Verification succeeded",
  otp_failed: "Verification failed",
  otp_expired: "Verification code expired",
  security_settings_changed: "Verification settings changed",
};

const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

function formatWhen(value) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  return date.toLocaleString();
}

function roleLabel(role) {
  return role === "administrator" ? "Administrator" : "User";
}

export function AdminDashboard() {
  const { user, logout } = useAuth();
  const { navigate } = useRouter();
  const [section, setSection] = useState("overview");
  const [signingOut, setSigningOut] = useState(false);

  async function signOut() {
    if (signingOut) return;
    setSigningOut(true);
    try {
      await logout();
    } catch {
      setSigningOut(false);
    }
  }

  return (
    <div className="shell admin-shell">
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-mark">DIRA</div>
          <div className="brand-name">Administration</div>
        </div>
        <nav>
          {SECTIONS.map(([id, label]) => (
            <button
              key={id}
              type="button"
              className={section === id ? "nav on" : "nav"}
              onClick={() => setSection(id)}
            >
              {label}
            </button>
          ))}
        </nav>
        <div className="sidebar-bottom">
          <div className="sidebar-user">
            <strong>{user.name}</strong>
            <span title={user.email}>{user.email}</span>
            <button type="button" className="sidebar-admin" onClick={() => navigate("/dashboard")}>
              CAT dashboard
            </button>
            <button type="button" onClick={signOut} disabled={signingOut}>
              {signingOut ? "Signing out..." : "Sign out"}
            </button>
          </div>
        </div>
      </aside>
      <main className="main admin-main">
        {section === "overview" ? <Overview /> : null}
        {section === "users" ? <Users currentUserId={user.id} /> : null}
        {section === "security" ? <Security /> : null}
        {section === "audit" ? <AuditLog /> : null}
      </main>
    </div>
  );
}

function Overview() {
  const [stats, setStats] = useState(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;
    getAdminStats()
      .then((next) => {
        if (!cancelled) setStats(next);
      })
      .catch((reason) => {
        if (!cancelled) setError(reason.message);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <section className="page">
      <header className="page-header">
        <div>
          <h1>Overview</h1>
          <p>Account records from the application database.</p>
        </div>
      </header>
      {error ? <p className="auth-error" role="alert">{error}</p> : null}
      {!stats && !error ? <p className="fine">Loading accounts…</p> : null}
      {stats ? (
        <div className="admin-kpis">
          <article><span>Total users</span><strong>{stats.total_users}</strong></article>
          <article><span>Active users</span><strong>{stats.active_users}</strong></article>
          <article><span>Inactive users</span><strong>{stats.inactive_users}</strong></article>
          <article><span>Administrators</span><strong>{stats.administrators}</strong></article>
        </div>
      ) : null}
    </section>
  );
}

const EMPTY_FORM = { first_name: "", last_name: "", email: "", phone: "", role: "user" };

function Users({ currentUserId }) {
  const [rows, setRows] = useState(null);
  const [error, setError] = useState("");
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState(EMPTY_FORM);
  const [formError, setFormError] = useState("");
  const [pending, setPending] = useState(false);
  const [issued, setIssued] = useState(null);
  const [busyId, setBusyId] = useState(null);

  async function load() {
    const payload = await getAdminUsers();
    setRows(payload.users);
  }

  useEffect(() => {
    let cancelled = false;
    getAdminUsers()
      .then((payload) => {
        if (!cancelled) setRows(payload.users);
      })
      .catch((reason) => {
        if (!cancelled) setError(reason.message);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  function update(field, value) {
    setForm((current) => ({ ...current, [field]: value }));
  }

  async function onCreate(event) {
    event.preventDefault();
    if (pending) return;
    if (!form.first_name.trim() || !form.last_name.trim()) {
      setFormError("First name and last name are required.");
      return;
    }
    if (!EMAIL.test(form.email.trim())) {
      setFormError("Enter a valid email address.");
      return;
    }
    if (!form.phone.trim()) {
      setFormError("Phone is required.");
      return;
    }
    setFormError("");
    setPending(true);
    try {
      const result = await createAdminUser({
        first_name: form.first_name.trim(),
        last_name: form.last_name.trim(),
        email: form.email.trim(),
        phone: form.phone.trim(),
        role: form.role,
      });
      setIssued({ email: result.user.email, password: result.temporary_password });
      setForm(EMPTY_FORM);
      setOpen(false);
      await load();
    } catch (reason) {
      setFormError(reason.message);
    } finally {
      setPending(false);
    }
  }

  async function toggle(row) {
    setBusyId(row.id);
    setError("");
    try {
      await setUserActive(row.id, !row.is_active);
      await load();
    } catch (reason) {
      setError(reason.message);
    } finally {
      setBusyId(null);
    }
  }

  return (
    <section className="page">
      <header className="page-header">
        <div>
          <h1>Users</h1>
          <p>Accounts created by an administrator.</p>
        </div>
        <button className="auth-submit admin-inline" type="button" onClick={() => setOpen((value) => !value)}>
          {open ? "Close" : "Create user"}
        </button>
      </header>
      {error ? <p className="auth-error" role="alert">{error}</p> : null}
      {issued ? (
        <div className="admin-secret" role="status">
          <p>Temporary password for {issued.email}. It is shown once and is not stored in the browser.</p>
          <strong>{issued.password}</strong>
          <button type="button" onClick={() => setIssued(null)}>Dismiss</button>
        </div>
      ) : null}
      {open ? (
        <form className="admin-form" onSubmit={onCreate} noValidate>
          <label>First name<input value={form.first_name} onChange={(event) => update("first_name", event.target.value)} disabled={pending} /></label>
          <label>Last name<input value={form.last_name} onChange={(event) => update("last_name", event.target.value)} disabled={pending} /></label>
          <label>Email<input type="email" value={form.email} onChange={(event) => update("email", event.target.value)} disabled={pending} /></label>
          <label>Phone<input value={form.phone} onChange={(event) => update("phone", event.target.value)} disabled={pending} /></label>
          <label>
            Role
            <select value={form.role} onChange={(event) => update("role", event.target.value)} disabled={pending}>
              <option value="user">User</option>
              <option value="administrator">Administrator</option>
            </select>
          </label>
          {formError ? <p className="auth-error" role="alert">{formError}</p> : null}
          <button className="auth-submit admin-inline" type="submit" disabled={pending}>
            {pending ? "Creating user..." : "Create user"}
          </button>
        </form>
      ) : null}
      {!rows && !error ? <p className="fine">Loading users…</p> : null}
      {rows ? (
        <div className="admin-table-wrap">
          <table className="admin-table">
            <thead>
              <tr>
                <th>Name</th>
                <th>Email</th>
                <th>Phone</th>
                <th>Role</th>
                <th>Status</th>
                <th>Last login</th>
                <th>Created</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.id}>
                  <td>{row.name}</td>
                  <td>{row.email}</td>
                  <td>{row.phone || "—"}</td>
                  <td>{roleLabel(row.role)}</td>
                  <td>{row.is_active ? "Active" : "Inactive"}</td>
                  <td>{formatWhen(row.last_login)}</td>
                  <td>{formatWhen(row.created_at)}</td>
                  <td>
                    <button
                      type="button"
                      disabled={busyId === row.id || row.id === currentUserId}
                      onClick={() => toggle(row)}
                    >
                      {busyId === row.id ? "Saving..." : row.is_active ? "Deactivate" : "Reactivate"}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </section>
  );
}

function Security() {
  const [emailOn, setEmailOn] = useState(null);
  const [smsOn, setSmsOn] = useState(null);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState(false);
  const [pending, setPending] = useState(false);

  useEffect(() => {
    let cancelled = false;
    getSecuritySettings()
      .then((payload) => {
        if (cancelled) return;
        setEmailOn(payload.email_otp_enabled);
        setSmsOn(payload.sms_otp_enabled);
      })
      .catch((reason) => {
        if (!cancelled) setError(reason.message);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  async function onSave(event) {
    event.preventDefault();
    if (pending || emailOn === null || smsOn === null) return;
    setError("");
    setSaved(false);
    setPending(true);
    try {
      const payload = await saveSecuritySettings({
        email_otp_enabled: emailOn,
        sms_otp_enabled: smsOn,
      });
      setEmailOn(payload.email_otp_enabled);
      setSmsOn(payload.sms_otp_enabled);
      setSaved(true);
    } catch (reason) {
      setError(reason.message);
    } finally {
      setPending(false);
    }
  }

  const bothOff = emailOn === false && smsOn === false;

  return (
    <section className="page">
      <header className="page-header">
        <div>
          <h1>Security</h1>
          <p>Choose which verification methods users can use when signing in.</p>
        </div>
      </header>
      {emailOn === null && !error ? <p className="fine">Loading security settings…</p> : null}
      {emailOn !== null ? (
        <form className="security-settings" onSubmit={onSave}>
          <div>
            <span className="event-label">Email OTP</span>
            <div className="segments" role="group" aria-label="Email OTP">
              <button type="button" className={emailOn ? "on" : ""} aria-pressed={emailOn} onClick={() => { setEmailOn(true); setSaved(false); }}>On</button>
              <button type="button" className={!emailOn ? "on" : ""} aria-pressed={!emailOn} onClick={() => { setEmailOn(false); setSaved(false); }}>Off</button>
            </div>
          </div>
          <div>
            <span className="event-label">SMS OTP</span>
            <div className="segments" role="group" aria-label="SMS OTP">
              <button type="button" className={smsOn ? "on" : ""} aria-pressed={smsOn} onClick={() => { setSmsOn(true); setSaved(false); }}>On</button>
              <button type="button" className={!smsOn ? "on" : ""} aria-pressed={!smsOn} onClick={() => { setSmsOn(false); setSaved(false); }}>Off</button>
            </div>
          </div>
          {bothOff ? (
            <p className="auth-note" role="status">
              Both verification methods are disabled. Users will sign in with email and password only.
            </p>
          ) : null}
          {error ? <p className="auth-error" role="alert">{error}</p> : null}
          {saved ? <p className="fine" role="status">Settings saved.</p> : null}
          <button className="auth-submit admin-inline" type="submit" disabled={pending}>
            {pending ? "Saving..." : "Save settings"}
          </button>
        </form>
      ) : null}
      {error && emailOn === null ? <p className="auth-error" role="alert">{error}</p> : null}
    </section>
  );
}

function AuditLog() {
  const [events, setEvents] = useState(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;
    getAuditLog()
      .then((payload) => {
        if (!cancelled) setEvents(payload.events);
      })
      .catch((reason) => {
        if (!cancelled) setError(reason.message);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <section className="page">
      <header className="page-header">
        <div>
          <h1>Audit log</h1>
          <p>Recent account events. Passwords are not recorded.</p>
        </div>
      </header>
      {error ? <p className="auth-error" role="alert">{error}</p> : null}
      {!events && !error ? <p className="fine">Loading audit log…</p> : null}
      {events ? (
        <div className="admin-table-wrap">
          <table className="admin-table">
            <thead>
              <tr>
                <th>When</th>
                <th>Action</th>
                <th>Actor</th>
                <th>Subject</th>
              </tr>
            </thead>
            <tbody>
              {events.length === 0 ? (
                <tr><td colSpan="4">No events yet.</td></tr>
              ) : events.map((event) => (
                <tr key={event.id}>
                  <td>{formatWhen(event.created_at)}</td>
                  <td>
                    {ACTION_LABELS[event.action] || event.action}
                    {event.detail ? ` · ${event.detail}` : ""}
                  </td>
                  <td>{event.actor_email || "—"}</td>
                  <td>{event.subject_email || "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </section>
  );
}
