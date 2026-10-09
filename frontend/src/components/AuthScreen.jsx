export function AuthScreen({ title, note, children, switchText, switchLabel, onSwitch }) {
  return (
    <main className="auth-screen">
      <section className="auth-panel">
        <p className="auth-mark">DIRA</p>
        <p className="auth-product">CAT Risk Intelligence</p>
        <h1>{title}</h1>
        {note ? <p className="auth-note">{note}</p> : null}
        {children}
        {switchLabel ? (
          <p className="auth-switch">
            {switchText}{" "}
            <button type="button" onClick={onSwitch}>
              {switchLabel}
            </button>
          </p>
        ) : null}
      </section>
    </main>
  );
}
