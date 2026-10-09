/**
 * The only module that talks to the backend.
 *
 * API_BASE is empty by default: the Vite dev server proxies /api to Django
 * (see vite.config.js). Set VITE_API_BASE_URL to call another origin; that
 * origin must be in the backend's CORS and CSRF allowlists.
 */

export const API_BASE = (import.meta.env.VITE_API_BASE_URL || "").replace(/\/+$/, "");
const DEFAULT_TIMEOUT_MS = 30000;
const RUN_TIMEOUT_MS = 180000;

/** An error from the backend, with its HTTP status and body when there is one. */
export class ApiError extends Error {
  constructor(message, { status = 0, body = null, retryAfter } = {}) {
    super(message);
    this.status = status;
    this.body = body;
    if (typeof retryAfter === "number") this.retryAfter = retryAfter;
  }
}

function url(path) {
  return `${API_BASE}${path}`;
}

async function request(path, options = {}, timeoutMs = DEFAULT_TIMEOUT_MS) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    return await fetch(url(path), { credentials: "include", ...options, signal: controller.signal });
  } catch (reason) {
    if (reason.name === "AbortError") throw new ApiError("The server took too long to respond.");
    throw new ApiError("The server could not be reached. Check that the backend is running.");
  } finally {
    clearTimeout(timer);
  }
}

async function readBody(response) {
  try {
    return await response.json();
  } catch {
    return {};
  }
}

function errorFrom(body, status) {
  const message = (body && typeof body.error === "string" && body.error) || "The request could not be completed.";
  return new ApiError(message, { status, body, retryAfter: body && body.retry_after });
}

async function readError(response) {
  return errorFrom(await readBody(response), response.status).message;
}

/** Parse a JSON response, or throw an ApiError with the backend's message. */
async function readJson(response) {
  const body = await readBody(response);
  if (!response.ok) throw errorFrom(body, response.status);
  if (!body || typeof body !== "object") throw new ApiError("The server returned an unexpected response.");
  return body;
}

export async function getCsrfToken() {
  const response = await request("/api/auth/csrf/");
  if (!response.ok) throw new ApiError(await readError(response), { status: response.status });
  const body = await response.json();
  if (!body.csrfToken) throw new ApiError("The authentication service did not issue a CSRF token.");
  return body.csrfToken;
}

async function sendJson(path, method, payload, timeoutMs) {
  const csrfToken = await getCsrfToken();
  return request(
    path,
    {
      method,
      headers: { "Content-Type": "application/json", "X-CSRFToken": csrfToken },
      body: JSON.stringify(payload),
    },
    timeoutMs,
  );
}

async function postJson(path, payload, timeoutMs) {
  return sendJson(path, "POST", payload, timeoutMs);
}

async function postForm(path, form, timeoutMs) {
  const csrfToken = await getCsrfToken();
  return request(path, { method: "POST", headers: { "X-CSRFToken": csrfToken }, body: form }, timeoutMs);
}

// --- Session ---------------------------------------------------------------------

export async function getCurrentUser() {
  const response = await request("/api/auth/me/");
  if (response.status === 401) return null;
  if (!response.ok) throw new ApiError(await readError(response), { status: response.status });
  return response.json();
}

export async function login(credentials) {
  return readJson(await postJson("/api/auth/login/", credentials));
}

export async function sendOtp(challengeId, method) {
  return readJson(await postJson("/api/auth/send-otp/", { challenge_id: challengeId, method }));
}

export async function resendOtp(challengeId) {
  return readJson(await postJson("/api/auth/resend-otp/", { challenge_id: challengeId }));
}

export async function verifyOtp(challengeId, code, method) {
  return readJson(await postJson("/api/auth/verify-otp/", { challenge_id: challengeId, code, method }));
}

export async function logout() {
  const response = await postJson("/api/auth/logout/", {});
  if (response.status === 401) return;
  if (!response.ok) throw new ApiError(await readError(response), { status: response.status });
}

// --- Administration --------------------------------------------------------------

export async function getSecuritySettings() {
  return readJson(await request("/api/admin/security/"));
}

export async function saveSecuritySettings(settings) {
  return readJson(await sendJson("/api/admin/security/", "PATCH", settings));
}

export async function getAdminStats() {
  return readJson(await request("/api/admin/stats/"));
}

export async function getAdminUsers() {
  return readJson(await request("/api/admin/users/"));
}

export async function createAdminUser(account) {
  return readJson(await postJson("/api/admin/users/", account));
}

export async function setUserActive(userId, isActive) {
  return readJson(await sendJson(`/api/admin/users/${userId}/`, "PATCH", { is_active: isActive }));
}

export async function getAuditLog() {
  return readJson(await request("/api/admin/audit-log/"));
}

// --- Supplied portfolio ----------------------------------------------------------

export async function loadPortfolio() {
  const response = await request("/api/portfolio/", {}, RUN_TIMEOUT_MS);
  if (!response.ok) throw new ApiError("The portfolio API did not respond.", { status: response.status });
  return response.json();
}

// --- Checkpoint 8 workflow -------------------------------------------------------

export async function getCapabilities() {
  return readJson(await request("/api/workflow/capabilities/"));
}

export async function getExampleSubmission() {
  return readJson(await request("/api/workflow/example/"));
}

export async function listSubmissions() {
  return readJson(await request("/api/workflow/submissions/"));
}

export async function getSubmission(id) {
  return readJson(await request(`/api/workflow/submissions/${encodeURIComponent(id)}/`));
}

/** Submit the bundled example, pasted text, or a UTF-8 text file. */
export async function createSubmission({ example = false, text = null, file = null }) {
  if (example) return readJson(await postJson("/api/workflow/submissions/", { source: "example" }, RUN_TIMEOUT_MS));
  if (file) {
    const form = new FormData();
    form.append("file", file);
    return readJson(await postForm("/api/workflow/submissions/", form, RUN_TIMEOUT_MS));
  }
  return readJson(await postJson("/api/workflow/submissions/", { text }, RUN_TIMEOUT_MS));
}

export async function recordDecision(id, decision) {
  return readJson(await postJson(`/api/workflow/submissions/${encodeURIComponent(id)}/decisions/`, decision));
}

export async function runSubmission(id) {
  return readJson(await postJson(`/api/workflow/submissions/${encodeURIComponent(id)}/run/`, {}, RUN_TIMEOUT_MS));
}

// --- Reference model runs --------------------------------------------------------

export async function listRuns() {
  return readJson(await request("/api/runs/"));
}

export async function startReferenceRun() {
  return readJson(await postJson("/api/runs/", {}, RUN_TIMEOUT_MS));
}

// --- File upload service (structured exposure files) -----------------------------

/** action is "adapt" (convert only) or "run" (convert, then run the engine). */
export async function uploadExposureFile(action, { file, synthetic, fxRate, acceptUncertain }) {
  const form = new FormData();
  form.append("file", file);
  if (synthetic) form.append("synthetic", synthetic);
  if (fxRate) form.append("fx_rate", String(fxRate));
  if (acceptUncertain) form.append("accept_uncertain", "true");
  const response = await postForm(`/api/ingest/${action}/`, form, RUN_TIMEOUT_MS);
  const body = await readBody(response);
  // A refusal (422) carries the adaptation report, which the screen shows.
  if (response.status === 422 && body && (body.report || body.adaptation_report)) return { ...body, httpStatus: 422 };
  if (!response.ok) throw errorFrom(body, response.status);
  return { ...body, httpStatus: response.status };
}
