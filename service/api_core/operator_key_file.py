"""Where the operator key comes from when `.env` does not set one: generated once, kept in a small volume.

WHY IT IS GENERATED. `OPERATOR_KEY` proves a request comes from an operator surface (the dashboard),
see `operator_authz.py`. Unset, it fails closed: the dashboard's delete controls refuse, and the service
cannot tell the operator sending AS an agent from the agent itself. Nothing ever set it -- `.env.example`
asked for `openssl rand -hex 32` by hand -- so it worked only on hosts where somebody had done that.

WHY NOT A FIXED DEFAULT. A default written in this repo is public, and a public secret proves nothing.

WHERE IT LIVES. `OPERATOR_KEY_DIR` (compose: a dedicated `operator-key` volume), else the data
directory. The dashboard is a separate process in its own container and injects the key into its page,
so it mounts that directory READ-ONLY and reads it here with `create=False`. A dedicated volume rather
than the data volume, so the dashboard container is not handed the database to reach one file.

ONLY THE SERVICE WRITES IT, at startup. A file that is missing, empty or too short to be a key is
replaced there -- an empty file left by an interrupted write would otherwise disable operator privilege
for good, silently. Any failure to write leaves it unset and is logged; it never stops the service.
Editing or deleting the file while the service runs takes effect at its next start.

`.env` STILL WINS. A key set there is used as is, and the file is neither read nor written.
"""

from __future__ import annotations

import logging
import os
import secrets
from pathlib import Path

logger = logging.getLogger(__name__)

OPERATOR_KEY_FILENAME = "operator.key"
#: Shorter than this is not a key this module wrote, and is not believed. A generated one is 64.
MIN_OPERATOR_KEY_LENGTH = 32


def operator_key_from_config(config, *, create: bool) -> str:
    """`resolve_operator_key` for a service config: the ONE place that decides which directory, so the
    service (which creates) and the dashboard (which only reads) cannot look in different ones."""
    return resolve_operator_key(config.operator_key, config.operator_key_dir or config.data_dir, create=create)


def resolve_operator_key(configured: str, key_dir, *, create: bool) -> str:
    """The key to use: `configured` if set, else the one in `key_dir`, (re)generating it when `create`.

    Returns "" when there is none and none can be made. That is the fail-closed answer: no caller
    can then claim operator privilege, exactly as before this existed.
    """
    configured = str(configured or "").strip()
    if configured:
        return configured
    path = Path(str(key_dir or "")) / OPERATOR_KEY_FILENAME
    existing = _read(path)
    if existing or not create:
        return existing
    key = secrets.token_hex(32)
    temporary = path.with_name(f".{OPERATOR_KEY_FILENAME}.{os.getpid()}.tmp")
    try:
        # Written aside and renamed into place, so a reader never sees a half-written key and an
        # interrupted write leaves the previous state rather than an empty file.
        fd = os.open(str(temporary), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(key + "\n")
        os.replace(temporary, path)
    except OSError as error:
        logger.warning(f"Could not write {path} ({error}); operator privilege stays unavailable.")
        try:
            temporary.unlink()
        except OSError:
            pass
        return ""
    logger.info(f"Generated an operator key at {path} (OPERATOR_KEY is not set in the environment).")
    return key


def _read(path: Path) -> str:
    try:
        text = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return ""
    except OSError as error:
        logger.warning(f"Could not read {path} ({error}); operator privilege stays unavailable.")
        return ""
    if len(text) < MIN_OPERATOR_KEY_LENGTH:
        if text:
            logger.warning(f"{path} does not hold a usable key; it is ignored (the service replaces it at start).")
        return ""
    return text
