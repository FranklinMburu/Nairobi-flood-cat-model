import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import {
  getCsrfToken,
  getCurrentUser,
  login as loginRequest,
  logout as logoutRequest,
  resendOtp as resendRequest,
  sendOtp as sendRequest,
  verifyOtp as verifyRequest,
} from "../services/api";

const AuthContext = createContext(null);

function challengeFrom(body) {
  return {
    challengeId: body.challenge_id,
    availableMethods: body.methods || [],
    method: body.method || "",
    maskedEmail: body.masked_email || "",
    maskedPhone: body.masked_phone || "",
    codeSent: Boolean(body.method),
  };
}

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null);
  const [loading, setLoading] = useState(true);
  const [challenge, setChallenge] = useState(null);

  const refreshUser = useCallback(async () => {
    const next = await getCurrentUser();
    setUser(next);
    return next;
  }, []);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        await getCsrfToken();
        const next = await getCurrentUser();
        if (!cancelled) setUser(next);
      } catch {
        if (!cancelled) setUser(null);
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const login = useCallback(async (credentials) => {
    const next = await loginRequest(credentials);
    if (next.requires_2fa) {
      setUser(null);
      setChallenge(challengeFrom(next));
      return next;
    }
    setChallenge(null);
    setUser(next);
    return next;
  }, []);

  const sendOtp = useCallback(async (method) => {
    const next = await sendRequest(challenge.challengeId, method);
    setChallenge(challengeFrom(next));
    return next;
  }, [challenge]);

  const resendOtp = useCallback(async () => {
    const next = await resendRequest(challenge.challengeId);
    setChallenge(challengeFrom(next));
    return next;
  }, [challenge]);

  const verifyOtp = useCallback(async (code) => {
    const next = await verifyRequest(challenge.challengeId, code, challenge.method);
    setChallenge(null);
    setUser(next);
    return next;
  }, [challenge]);

  const clearChallenge = useCallback(() => {
    setChallenge(null);
  }, []);

  const logout = useCallback(async () => {
    await logoutRequest();
    setChallenge(null);
    setUser(null);
  }, []);

  const value = useMemo(
    () => ({
      user,
      loading,
      isAuthenticated: Boolean(user),
      requires2FA: Boolean(challenge),
      challengeId: challenge ? challenge.challengeId : "",
      availableMethods: challenge ? challenge.availableMethods : [],
      method: challenge ? challenge.method : "",
      maskedEmail: challenge ? challenge.maskedEmail : "",
      maskedPhone: challenge ? challenge.maskedPhone : "",
      codeSent: challenge ? challenge.codeSent : false,
      login,
      sendOtp,
      resendOtp,
      verifyOtp,
      clearChallenge,
      logout,
      refreshUser,
    }),
    [user, loading, challenge, login, sendOtp, resendOtp, verifyOtp, clearChallenge, logout, refreshUser],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const value = useContext(AuthContext);
  if (!value) throw new Error("useAuth must be used within AuthProvider.");
  return value;
}
