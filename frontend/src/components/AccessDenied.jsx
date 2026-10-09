import { useRouter } from "../routing";

export function AccessDenied() {
  const { navigate } = useRouter();
  return (
    <main className="auth-screen">
      <section className="auth-panel">
        <p className="auth-mark">DIRA</p>
        <p className="auth-product">CAT Risk Intelligence</p>
        <h1>Access denied</h1>
        <p className="auth-note">This area is limited to administrators.</p>
        <button className="auth-submit" type="button" onClick={() => navigate("/dashboard")}>
          Return to dashboard
        </button>
      </section>
    </main>
  );
}
