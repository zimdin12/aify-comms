// What a bare TCP connection to a service's port meets, for the one question `fetch` cannot answer.
//
// WHY IT EXISTS. The doctor's `service` row tells a dead Docker port forward (a WSL restart left the
// forward accepting and dropping every connection while the container runs on) from a service that is
// not running, by the error `fetch` raises. That works where the drop surfaces as ECONNRESET or
// UND_ERR_SOCKET. MEASURED 2026-09-23 on Windows, node 22.20: a peer that closes right after accept
// makes `fetch` hang until its timeout and report no cause at all -- the same answer a hung service
// gives -- so the row said "start it" to an operator whose service was up. A raw connection separates
// the two without sending a byte: the forward closes it, a live service holds it open waiting for a
// request, and a closed port refuses it.
import net from "node:net";

/**
 * @returns {Promise<"dropped"|"silent"|"refused"|"answered"|"error">}
 *   dropped  -- the connection was accepted and the far side closed or reset it unprompted
 *   silent   -- accepted and held open: something is listening and waiting, which is not a drop
 *   refused  -- nothing is listening
 *   answered -- the far side spoke first (not HTTP's habit, but plainly not a drop)
 *   error    -- no connection was made for another reason, or the URL was unusable
 */
export function portFate(url, { timeoutMs = 1500, connect = net.connect } = {}) {
  return new Promise((resolve) => {
    let target;
    try { target = new URL(url); } catch { resolve("error"); return; }
    const port = Number(target.port || (target.protocol === "https:" ? 443 : 80));
    const host = target.hostname.replace(/^\[|\]$/g, "");
    let connected = false;
    let settled = false;
    const socket = connect({ host, port });
    const finish = (fate) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      socket.destroy();
      resolve(fate);
    };
    const timer = setTimeout(() => finish(connected ? "silent" : "error"), timeoutMs);
    socket.once("connect", () => { connected = true; });
    socket.once("data", () => finish("answered"));
    socket.once("end", () => finish(connected ? "dropped" : "error"));
    socket.once("close", () => finish(connected ? "dropped" : "error"));
    // A reset can arrive before the `connect` event is delivered, and it still means something
    // accepted the connection: nothing resets a connection that was never made.
    socket.once("error", (error) => finish(
      error?.code === "ECONNREFUSED" ? "refused"
        : error?.code === "ECONNRESET" || connected ? "dropped"
          : "error"));
  });
}
