// Native hook fixture: every fetch is intercepted. No request can reach a live endpoint.
import fs from "node:fs";

const allowed = JSON.parse(process.env.RESIDENT_TEST_ROUTES);
globalThis.fetch = async (url, options) => {
  const request = {
    url: String(url), method: options.method, headers: options.headers,
    body: JSON.parse(options.body), redirect: options.redirect,
    credentials: options.credentials, hasSignal: options.signal instanceof AbortSignal,
  };
  fs.appendFileSync(process.env.RESIDENT_TEST_CAPTURE, JSON.stringify(request) + "\n");
  if (!allowed.includes(request.url) || request.method !== "POST") {
    throw new Error("Unowned test route refused");
  }
  if (process.env.RESIDENT_TEST_HANG === "1") {
    // A real socket keeps Node alive; the injected transport must do the same.
    const keepAlive = setTimeout(() => {}, 5000);
    try {
      await new Promise((resolve, reject) => {
        options.signal.addEventListener("abort", () => reject(options.signal.reason), { once: true });
      });
    } finally { clearTimeout(keepAlive); }
  }
  return new Response(null, { status: Number(process.env.RESIDENT_TEST_STATUS || 200) });
};
