import { useCallback, useEffect, useState } from "react";

const BASE = "/api";

class ApiError extends Error {
  constructor(message, status) {
    super(message);
    this.status = status;
  }
}

async function request(path, options = {}) {
  const res = await fetch(`${BASE}${path}`, {
    headers: options.body instanceof FormData ? {} : { "Content-Type": "application/json" },
    ...options,
  });

  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`;
    try {
      const body = await res.json();
      // FastAPI puts validation errors in an array of objects.
      if (Array.isArray(body.detail)) detail = body.detail.map((d) => d.msg).join("; ");
      else if (body.detail) detail = body.detail;
    } catch {
      /* non-JSON error body — keep the status line */
    }
    throw new ApiError(detail, res.status);
  }
  return res.status === 204 ? null : res.json();
}

const qs = (params) => {
  const s = new URLSearchParams(
    Object.entries(params).filter(([, v]) => v !== "" && v != null && v !== false),
  ).toString();
  return s ? `?${s}` : "";
};

export const api = {
  periods: () => request("/periods"),
  kpis: (period) => request(`/kpis${qs({ period })}`),
  summary: (period) => request(`/summary${qs({ period })}`),
  excluded: (period) => request(`/excluded${qs({ period })}`),
  billingCustomers: (period) => request(`/billing-customers${qs({ period })}`),
  events: (params) => request(`/events${qs(params)}`),

  reviewQueue: (period) => request(`/review-queue${qs({ period })}`),
  saveOverride: (body) => request("/overrides", { method: "PUT", body: JSON.stringify(body) }),
  revokeOverride: (id, actor) =>
    request(`/overrides/${encodeURIComponent(id)}${qs({ actor })}`, { method: "DELETE" }),
  exportOverrides: () => request("/overrides/export"),
  importOverrides: (payload) =>
    request("/overrides/import", { method: "POST", body: JSON.stringify(payload) }),
  auditTrail: () => request("/overrides/audit"),

  config: () => request("/config"),
  runPipeline: (period) => request(`/pipeline/run${qs({ period })}`, { method: "POST" }),
  comparison: (period) => request(`/comparison/latest${qs({ period })}`),
};

/** Fetch on mount, expose a refetch. `deps` controls re-fetching. */
export function useApi(fn, deps = [], initial = null) {
  const [data, setData] = useState(initial);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setData(await fn());
    } catch (e) {
      setError(e.message || "Request failed");
    } finally {
      setLoading(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      setLoading(true);
      setError(null);
      try {
        const d = await fn();
        if (!cancelled) setData(d);
      } catch (e) {
        if (!cancelled) setError(e.message || "Request failed");
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  return { data, loading, error, refetch: load };
}
