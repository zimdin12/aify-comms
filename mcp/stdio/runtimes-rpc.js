// runtimes-rpc.js — JSON-RPC clients (stdio + WebSocket) used by the codex
// app-server integration and other runtime controllers. Extracted verbatim
// from runtimes.js (task #123). runtimes.js re-exports the public surface.
import readline from "readline";
import WebSocket from "ws";

export function quoteForDisplay(text) {
  return String(text || "").replace(/\s+/g, " ").trim();
}

export function createRpcClient(proc, { onNotification, onStderr } = {}) {
  const pending = new Map();
  let nextId = 1;
  let processError = null;
  // Mutable notification handler so a pooled RPC (CodexSession) can swap
  // it per turn without rebuilding the client. Defaults to the
  // constructor-time `onNotification`; null disables forwarding.
  let activeNotificationHandler = onNotification || null;
  let activeStderrHandler = onStderr || null;

  function failPending(error) {
    for (const [id, pendingRequest] of pending.entries()) {
      pending.delete(id);
      pendingRequest.reject(error);
    }
  }

  proc.on("error", (error) => {
    processError = error instanceof Error ? error : new Error(String(error));
    failPending(processError);
    if (activeStderrHandler) activeStderrHandler(processError.message || String(processError));
  });

  const stdout = readline.createInterface({ input: proc.stdout });
  stdout.on("line", (line) => {
    const text = line.trim();
    if (!text) return;
    let message;
    try {
      message = JSON.parse(text);
    } catch {
      return;
    }

    if (Object.prototype.hasOwnProperty.call(message, "id")) {
      const pendingRequest = pending.get(message.id);
      if (!pendingRequest) return;
      pending.delete(message.id);
      if (message.error) pendingRequest.reject(new Error(message.error.message || JSON.stringify(message.error)));
      else pendingRequest.resolve(message.result);
      return;
    }

    if (message.method && activeNotificationHandler) {
      activeNotificationHandler(message);
    }
  });

  const stderr = readline.createInterface({ input: proc.stderr });
  stderr.on("line", (line) => {
    if (activeStderrHandler) activeStderrHandler(line);
  });

  function send(payload) {
    proc.stdin.write(`${JSON.stringify(payload)}\n`);
  }

  function request(method, params, timeoutMs = 30000) {
    return new Promise((resolve, reject) => {
      if (processError) {
        reject(processError);
        return;
      }
      const id = nextId++;
      const timer = setTimeout(() => {
        pending.delete(id);
        reject(new Error(`${method} timed out after ${timeoutMs}ms`));
      }, timeoutMs);
      pending.set(id, {
        resolve: (result) => {
          clearTimeout(timer);
          resolve(result);
        },
        reject: (error) => {
          clearTimeout(timer);
          reject(error);
        },
      });
      send({ jsonrpc: "2.0", id, method, params });
    });
  }

  function notify(method, params) {
    send({ jsonrpc: "2.0", method, params });
  }

  function setOnNotification(handler) {
    activeNotificationHandler = typeof handler === "function" ? handler : null;
  }

  function setOnStderr(handler) {
    activeStderrHandler = typeof handler === "function" ? handler : null;
  }

  function close() {
    failPending(new Error("rpc client closed"));
    activeNotificationHandler = null;
    activeStderrHandler = null;
    try { stdout.close(); } catch {}
    try { stderr.close(); } catch {}
    try { proc.stdin?.end?.(); } catch {}
    try { proc.stdout?.destroy?.(); } catch {}
    try { proc.stderr?.destroy?.(); } catch {}
  }

  return { request, notify, setOnNotification, setOnStderr, close };
}

export function createWebSocketRpcClient(url, { token, onNotification, onStderr } = {}) {
  return new Promise((resolve, reject) => {
    const pending = new Map();
    let nextId = 1;
    let opened = false;
    let closed = false;

    let activeNotificationHandler = onNotification || null;
    let activeStderrHandler = onStderr || null;
    const headers = {};
    if (token) headers.Authorization = `Bearer ${token}`;
    const socket = new WebSocket(url, Object.keys(headers).length ? { headers } : undefined);

    function failPending(error) {
      for (const [id, pendingRequest] of pending.entries()) {
        pending.delete(id);
        pendingRequest.reject(error);
      }
    }

    function onSocketFailure(error) {
      if (!closed) {
        closed = true;
        failPending(error);
      }
      if (!opened) {
        reject(error);
      } else if (onStderr) {
        onStderr(error.message || String(error));
      }
    }

    socket.on("open", () => {
      opened = true;

      function send(payload) {
        if (socket.readyState !== WebSocket.OPEN) {
          throw new Error("Codex WebSocket app-server connection is not open");
        }
        socket.send(JSON.stringify(payload));
      }

      function request(method, params, timeoutMs = 30000) {
        return new Promise((resolveRequest, rejectRequest) => {
          if (socket.readyState !== WebSocket.OPEN) {
            rejectRequest(new Error("Codex WebSocket app-server connection is not open"));
            return;
          }

          const id = nextId++;
          const timer = setTimeout(() => {
            pending.delete(id);
            rejectRequest(new Error(`${method} timed out after ${timeoutMs}ms`));
          }, timeoutMs);

          pending.set(id, {
            resolve: (result) => {
              clearTimeout(timer);
              resolveRequest(result);
            },
            reject: (error) => {
              clearTimeout(timer);
              rejectRequest(error);
            },
          });

          send({ jsonrpc: "2.0", id, method, params });
        });
      }

      function notify(method, params) {
        send({ jsonrpc: "2.0", method, params });
      }

      function close() {
        if (socket.readyState === WebSocket.OPEN || socket.readyState === WebSocket.CONNECTING) {
          socket.close();
        }
        activeNotificationHandler = null;
        activeStderrHandler = null;
      }

      function setOnNotification(handler) {
        activeNotificationHandler = typeof handler === "function" ? handler : null;
      }

      function setOnStderr(handler) {
        activeStderrHandler = typeof handler === "function" ? handler : null;
      }

      resolve({ request, notify, close, setOnNotification, setOnStderr });
    });

    socket.on("message", (data) => {
      let message;
      try {
        message = JSON.parse(String(data));
      } catch {
        return;
      }

      if (Object.prototype.hasOwnProperty.call(message, "id")) {
        const pendingRequest = pending.get(message.id);
        if (!pendingRequest) return;
        pending.delete(message.id);
        if (message.error) pendingRequest.reject(new Error(message.error.message || JSON.stringify(message.error)));
        else pendingRequest.resolve(message.result);
        return;
      }

      if (message.method && activeNotificationHandler) {
        activeNotificationHandler(message);
      }
    });

    socket.on("error", (error) => {
      onSocketFailure(error instanceof Error ? error : new Error(String(error)));
    });

    socket.on("close", (code, reasonBuffer) => {
      const reasonText = quoteForDisplay(
        Buffer.isBuffer(reasonBuffer) ? reasonBuffer.toString("utf-8") : String(reasonBuffer || ""),
      );
      const detail = reasonText || `Codex WebSocket app-server connection closed (${code})`;
      onSocketFailure(new Error(detail));
    });
  });
}

export function codexAppServerReachable(url, { token, timeoutMs = 1200 } = {}) {
  const target = String(url || "").trim();
  if (!target) return Promise.resolve(false);
  return new Promise((resolve) => {
    let settled = false;
    const headers = {};
    if (token) headers.Authorization = `Bearer ${token}`;
    let socket;
    const finish = (ok) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      try {
        if (socket && (socket.readyState === WebSocket.OPEN || socket.readyState === WebSocket.CONNECTING)) {
          socket.close();
        }
      } catch {
        // best effort
      }
      resolve(Boolean(ok));
    };
    const timer = setTimeout(() => finish(false), Math.max(250, Number(timeoutMs) || 1200));
    try {
      socket = new WebSocket(target, Object.keys(headers).length ? { headers } : undefined);
      socket.on("open", () => finish(true));
      socket.on("error", () => finish(false));
      socket.on("close", () => finish(false));
    } catch {
      finish(false);
    }
  });
}
