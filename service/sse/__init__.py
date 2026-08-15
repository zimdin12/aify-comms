"""Leaf modules the SSE transport imports.

WHY THEY LIVE UNDER `service/` AND NOT BESIDE `mcp/sse_server.py`, which is the obvious place and
the wrong one: **`mcp/` is not this repo's package.** `import mcp` resolves to the PyPI `mcp`
distribution — the one `sse_server.py` itself uses for `from mcp.server.fastmcp import FastMCP` —
and the repo directory has no `__init__.py` precisely so it stays that way. That is also why
`service/main.py` loads the transport by FILE PATH through `importlib.util.spec_from_file_location`
rather than importing it. A sibling module dropped next to the transport could not be imported by
name at all, and adding an `__init__.py` to `mcp/` to fix that would shadow the very distribution
FastMCP comes from.

So the transport is decomposed INTO the importable package. These modules are leaves: they import
`service.config` and third-party code, never a router, never the control plane, and never
`sse_server` itself — the transport is the caller.
"""
