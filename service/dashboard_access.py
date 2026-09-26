"""Who may open the dashboard on 8801: a browser that has shown the service's API key (v0.7.4).

THE HOLE. The page on 8801 carries the operator key, injected server-side (`new_dashboard_app.py`), and
it was served to anything that asked. The key prompt appeared only later, when the page's own calls to
the service came back 401, so anyone who could reach 8801 was the operator. The operator's ruling,
2026-09-26: "dashboard has too much control, so it should be under API_KEY protection".

THE GATE. With `API_KEY` set, `/` is served to a browser holding the session cookie, or arriving with a
valid `?api_key=` (the bookmark shape the service port documents); anything else gets the login page.
The cookie holds a keyed hash of the API key, never the key, so changing `API_KEY` signs every browser
out. With no `API_KEY` there is no authentication anywhere, and this gate adds none: the service itself
is open then too.
"""

from __future__ import annotations

import hashlib
import hmac
import re
from html import escape

COOKIE = "aify_dashboard"
COOKIE_MAX_AGE = 180 * 24 * 3600


def session_token(api_key: str) -> str:
    """What the cookie holds: derived from the key, so the key itself never sits in a cookie."""
    return hmac.new(api_key.encode("utf-8"), b"aify-dashboard-session-v1", hashlib.sha256).hexdigest()


def key_matches(api_key: str, presented: str) -> bool:
    """Constant time, and never true for an empty key on either side."""
    if not api_key or not presented:
        return False
    return hmac.compare_digest(api_key.encode("utf-8", "ignore"), presented.encode("utf-8", "ignore"))


def is_authorized(api_key: str, *, cookie: str = "", query_key: str = "") -> bool:
    if not api_key:
        return True
    if cookie and hmac.compare_digest(cookie.encode("utf-8", "ignore"), session_token(api_key).encode("utf-8")):
        return True
    return key_matches(api_key, query_key)


_API_PORT = re.compile(r'<html[^>]*\bdata-default-api-port="(\d+)"')


def login_page(index_html: str, *, refused: bool = False) -> str:
    """The login form. It also stores the key where the dashboard's own calls read it (api-key.mjs), so
    the page it leads to does not ask a second time. The service port is read from index.html's own
    attribute, which is what `defaultApiOrigin` reads there."""
    port = (_API_PORT.search(index_html) or [None, "8800"])[1]
    note = '<p class="err">That key was refused.</p>' if refused else ""
    return f"""<!doctype html>
<html lang="en" data-default-api-port="{escape(port)}">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>AIFY Comms - sign in</title>
  <style>
    body {{ margin: 0; min-height: 100vh; display: flex; align-items: center; justify-content: center;
      background: #0f1115; color: #e6e8ec; font: 14px system-ui, -apple-system, Segoe UI, sans-serif; }}
    form {{ background: #171a21; padding: 24px; border-radius: 10px; min-width: 300px; display: flex;
      flex-direction: column; gap: 12px; }}
    input, button {{ font: inherit; padding: 8px 10px; border-radius: 6px; border: 1px solid #333a46;
      background: #0f1115; color: inherit; }}
    button {{ background: #2d5bd7; border-color: #2d5bd7; cursor: pointer; }}
    .err {{ color: #ff8a8a; margin: 0; }}
  </style>
</head>
<body>
  <form id="login" method="post" action="/login">
    <strong>AIFY Comms</strong>
    <label for="key">API key</label>
    <input id="key" name="key" type="password" autocomplete="current-password" required autofocus>
    {note}
    <button type="submit">Open the dashboard</button>
  </form>
  <script type="module">
    import {{ defaultApiOrigin }} from "/assets/api-origin.mjs";
    import {{ writeApiKey }} from "/assets/api-key.mjs";
    document.getElementById("login").addEventListener("submit", () => {{
      writeApiKey(document.getElementById("key").value, defaultApiOrigin());
    }});
  </script>
</body>
</html>
"""
