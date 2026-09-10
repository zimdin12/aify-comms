# HTTPS status and validation

## What exists

The optional `https` Compose profile runs Caddy with a persistent local CA. It is not enabled by a
normal Compose start. `config/Caddyfile` routes `/api/*`, `/ws`, and `/version` to `service:8800`;
the remaining paths go to `new-dashboard:8801`. Caddy terminates TLS and supports WebSocket upgrades.
Plain HTTP between those containers is not browser mixed content.

The dashboard defaults to the page origin under HTTPS. Its realtime client converts that origin to
`wss:`. API requests and terminal input use the same API base. An explicit HTTPS `apiOrigin` override
remains supported; an HTTP override from either the query string or stored settings is now refused
on an HTTPS page. A rejected stored HTTP value is removed. The client does not guess that an HTTP
backend port also speaks TLS. HTTP-page overrides are unchanged.

The reproduced defect was that overrides bypassed the existing same-origin HTTPS fallback. An HTTPS
bookmark containing `?apiOrigin=http://other:8800` persisted that HTTP origin and selected `ws:` for
realtime. Removing the query did not repair that HTTPS origin's stored setting. Local storage is
origin-specific; settings on an HTTP origin do not automatically migrate to an HTTPS origin.

## What this change proves

Run from the repository root with Node 22 or newer and OpenSSL on PATH:

```bash
node --test service/new_dashboard/api-origin.test.mjs service/new_dashboard/https-origin.test.mjs service/new_dashboard/realtime-socket.test.mjs service/new_dashboard/clipboard.test.mjs service/new_dashboard/xterm-mount-handlers.test.mjs service/new_dashboard/extraction-proof.test.mjs
```

`https-origin.test.mjs` generates a one-day localhost fixture certificate in a temporary directory.
Only its child Node process trusts that certificate, via `NODE_EXTRA_CA_CERTS`. It deletes the key
and certificate after the test. It never installs a CA, disables certificate validation, reads
operator credentials, or connects to the running stack.

The fixture exercises the production origin resolver, API client, serialized terminal input poster,
and realtime client against an ephemeral loopback HTTPS server. It verifies an HTTPS API response,
a terminal-input POST, an actual WSS upgrade and received event, and one common host/port for all
three requests. Separate controls require an untrusted certificate and a wrong hostname to fail.
The fixture endpoint responses are test data, not evidence about the deployed backend.

Clipboard tests cover the secure async-copy path and insecure/permission-denied copy fallback.
Mounted-terminal handler tests cover Ctrl+V and Ctrl+Shift+V with secure and insecure context flags:
no async clipboard read, no cancelled browser default, and one modeled native-paste onData emission
through the input poster. These are handler tests, not browser secure-context or OS clipboard proof.
No browser or deployed Caddy instance was run in this validation.

## Deployment still needs operator input

The committed September 7 v0.6.3 plan recorded an IP-matching certificate but an untrusted local CA.
That was a historical observation, not a current trust-store check. This code change does not import
that CA or establish that the operator's current HTTPS URL works.

Before changing a deployment, obtain the exact URL the user opens, including hostname or IP and
port, and the client device/browser. Then check:

- `HTTPS_PORT` and every port in `HTTPS_SITES` must agree. Compose's effective default sites are
  `localhost:8443, 127.0.0.1:8443`; Compose explicitly supplies this value, overriding the longer
  fallback in the Caddyfile. Changing only `HTTPS_PORT` is insufficient.
- Every reached name or IP must appear in `HTTPS_SITES`, which also feeds backend trusted-host
  policy. For IP access through Docker, set `HTTPS_DEFAULT_SNI` to that IP so a client with no SNI
  receives the intended certificate. Do not infer the user's LAN address from historical docs.
- Trust the current Caddy CA on the actual client device/browser, using the export and trust
  procedure in the README. Confirm certificate identity before importing a root. Do not use a
  warning bypass or disable TLS verification as the remedy.
- Verify the actual page has no certificate warning, `window.isSecureContext` is true, the API
  requests use the intended HTTPS origin, and `/ws` upgrades via WSS. Test copy and native paste in
  the browser after that. A healthy HTTP dashboard on port 8811 does not prove these conditions.

A public domain is not required for the existing local-CA design. Publicly trusted TLS would need
a separately chosen hostname, certificate strategy, and deployment approval. TLS is also not
access control; do not expose the control plane publicly merely because it has a certificate.

## Scope limit

The legacy resident Codex synthetic console accepts a runtime-provided `appServerUrl` directly in
`codex-console.mjs`. That is not the dashboard `/ws` channel tested here. An advertised `ws:` URL
there is not made HTTPS-compatible by this change; nor does changing it to `wss:` create a TLS
listener. A deployment relying on that path needs a reachable TLS endpoint/proxy for that runtime.
Do not report all optional embedded/runtime consoles as HTTPS-verified from the dashboard fixture.
