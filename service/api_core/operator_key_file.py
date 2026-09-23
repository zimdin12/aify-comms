"""Where the operator key comes from when `.env` does not set one: generated once, kept in the data volume.

WHY IT IS GENERATED. `OPERATOR_KEY` proves a request comes from an operator surface (the dashboard),
see `operator_authz.py`. Unset, it fails closed: the dashboard's delete controls refuse, and the service
cannot tell the operator sending AS an agent from the agent itself. Nothing ever set it -- `.env.example`
asked for `openssl rand -hex 32` by hand -- so it worked only on hosts where somebody had done that.

WHY NOT A FIXED DEFAULT. A default written in this repo is public, and a public secret proves nothing.

WHO READS THE FILE. The service creates it at startup. The dashboard is a separate process in its own
container and injects the key into its page, so it mounts the same volume READ-ONLY and reads it here
with `create=False` -- it never writes one, which is what keeps a single key for both.

`.env` STILL WINS. A key set there is used as is, and the file is neither read nor written.
"""

from __future__ import annotations

import logging
import os
import secrets
from pathlib import Path

logger = logging.getLogger(__name__)

OPERATOR_KEY_FILENAME = "operator.key"


def resolve_operator_key(configured: str, data_dir, *, create: bool) -> str:
    """The key to use: `configured` if set, else the one in `data_dir`, generating it when `create`.

    Returns "" when there is none and none can be made. That is the fail-closed answer: no caller
    can then claim operator privilege, exactly as before this existed.
    """
    configured = str(configured or "").strip()
    if configured:
        return configured
    path = Path(str(data_dir or "")) / OPERATOR_KEY_FILENAME
    existing = _read(path)
    if existing or not create:
        return existing
    key = secrets.token_hex(32)
    try:
        # O_EXCL: if two processes race to create it, exactly one writes and the other reads the
        # winner's key, so they can never end up holding different keys.
        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return _read(path)
    except OSError as error:
        logger.warning(f"Could not create {path} ({error}); operator privilege stays unavailable.")
        return ""
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(key + "\n")
    logger.info(f"Generated an operator key at {path} (OPERATOR_KEY is not set in the environment).")
    return key


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return ""
    except OSError as error:
        logger.warning(f"Could not read {path} ({error}); operator privilege stays unavailable.")
        return ""
