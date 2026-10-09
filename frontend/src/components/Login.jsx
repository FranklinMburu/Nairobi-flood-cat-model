import { useEffect, useState } from "react";
import { useAuth } from "../context/AuthContext";
import { AuthScreen } from "./AuthScreen";

const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

export function Login() {
  const {
    login,
    sendOtp,
    resendOtp,
    verifyOtp,
    clearChallenge,
    requires2FA,
    availableMethods,
    method,
    maskedEmail,
    maskedPhone,
    codeSent,
  } = useAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [choice, setChoice] = useState("email");
  const [code, setCode] = useState("");
  const [error, setError] = useState("");
  const [pending, setPending] = useState(false);
  const [wait, setWait] = useState(0);

  useEffect(() => {
    if (codeSent) setWait(60);
  }, [codeSent, method]);

  useEffect(() => {
    if (wait <= 0) return undefined;
    const timer = setInterval(() => {
      setWait((value) => (value > 0 ? value - 1 : 0));
    }, 1000);
    return () => clearInterval(timer);
  }, [wait]);

  async function onPassword(event) {
    event.preventDefault();
    if (pending) return;
    const trimmed = email.trim();
    if (!trimmed || !password) {
      setError("Email and password are required.");
      return;
    }
    if (!EMAIL.test(trimmed)) {
      setError("Enter a valid email address.");
      return;
    }
    setError("");
    setPending(true);
    try {
      await login({ email: trimmed, password });
      setPassword("");
    } catch (reason) {
      setError(reason.message);
    } finally {
      setPending(false);
    }
  }

  async function onChoose(event) {
    event.preventDefault();
    if (pending) return;
    setError("");
    setPending(true);
    try {
      await sendOtp(choice);
    } catch (reason) {
      setError(reason.message);
    } finally {
      setPending(false);
    }
  }

  async function onVerify(event) {
    event.preventDefault();
    if (pending) return;
    if (!/^\d{6}$/.test(code)) {
      setError("Enter the 6-digit verification code.");
      return;
    }
    setError("");
    setPending(true);
    try {
      await verifyOtp(code);
    } catch (reason) {
      setError(reason.message);
      setPending(false);
    }
  }

  async function onResend() {
    if (pending || wait > 0) return;
    setError("");
    setPending(true);
    try {
      await resendOtp();
      setWait(60);
    } catch (reason) {
      if (reason.retryAfter) setWait(reason.retryAfter);
      setError(reason.message);
    } finally {
      setPending(false);
    }
  }

  async function onSwitchMethod(next) {
    if (pending || wait > 0) return;
    setError("");
    setPending(true);
    try {
      await sendOtp(next);
      setCode("");
      setWait(60);
    } catch (reason) {
      if (reason.retryAfter) setWait(reason.retryAfter);
      setError(reason.message);
    } finally {
      setPending(false);
    }
  }

  if (requires2FA && !codeSent) {
    return (
      <AuthScreen title="Verify your identity" note="How would you like to receive your verification code?">
        <form className="auth-form" onSubmit={onChoose}>
          <div className="otp-choice" role="group" aria-label="Verification method">
            {availableMethods.includes("email") ? (
              <button type="button" className={choice === "email" ? "on" : ""} onClick={() => setChoice("email")}>
                Email
                <small>{maskedEmail}</small>
              </button>
            ) : null}
            {availableMethods.includes("sms") ? (
              <button type="button" className={choice === "sms" ? "on" : ""} onClick={() => setChoice("sms")}>
                SMS
                <small>{maskedPhone}</small>
              </button>
            ) : null}
          </div>
          {error ? <p className="auth-error" role="alert">{error}</p> : null}
          <button className="auth-submit" type="submit" disabled={pending}>
            {pending ? "Sending..." : "Continue"}
          </button>
          <button
            className="auth-link"
            type="button"
            onClick={() => {
              clearChallenge();
              setError("");
            }}
          >
            Use a different account
          </button>
        </form>
      </AuthScreen>
    );
  }

  if (requires2FA) {
    const destination = method === "sms" ? maskedPhone : maskedEmail;
    const other = method === "sms" ? "email" : "sms";
    return (
      <AuthScreen title="Enter your code" note={`Sent to ${destination}. It expires in 5 minutes.`}>
        <form className="auth-form" onSubmit={onVerify}>
          <label>
            Verification code
            <input
              inputMode="numeric"
              autoComplete="one-time-code"
              maxLength={6}
              value={code}
              onChange={(event) => setCode(event.target.value.replace(/\D/g, "").slice(0, 6))}
              disabled={pending}
            />
          </label>
          {error ? <p className="auth-error" role="alert">{error}</p> : null}
          <button className="auth-submit" type="submit" disabled={pending}>
            {pending ? "Checking..." : "Verify"}
          </button>
          <button className="auth-link" type="button" onClick={onResend} disabled={pending || wait > 0}>
            {wait > 0 ? `Resend code (${wait}s)` : "Resend code"}
          </button>
          {availableMethods.length > 1 ? (
            <button className="auth-link" type="button" onClick={() => onSwitchMethod(other)} disabled={pending || wait > 0}>
              {other === "sms" ? "Send by SMS instead" : "Send by email instead"}
            </button>
          ) : null}
        </form>
      </AuthScreen>
    );
  }

  return (
    <AuthScreen title="Sign in" note="Accounts are issued by your administrator.">
      <form className="auth-form" onSubmit={onPassword} noValidate>
        <label>
          Email
          <input
            type="email"
            name="email"
            autoComplete="email"
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            disabled={pending}
          />
        </label>
        <label>
          Password
          <input
            type="password"
            name="password"
            autoComplete="current-password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            disabled={pending}
          />
        </label>
        {error ? <p className="auth-error" role="alert">{error}</p> : null}
        <button className="auth-submit" type="submit" disabled={pending}>
          {pending ? "Signing in..." : "Sign in"}
        </button>
      </form>
    </AuthScreen>
  );
}
