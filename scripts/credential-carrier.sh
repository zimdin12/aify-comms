#!/bin/bash
# Hand this service's key to aify-env's credential store, and report what converged.
#
#   bash scripts/credential-carrier.sh <key-on-stdin>
#
# WHY THE KEY GOES TO ANOTHER TIER AT ALL. aify-env advertises this host to this service, and its
# credential comes only from its own process environment -- which nothing on this host puts there,
# because the aify-comms bridge does not start that daemon. So the moment `API_KEY` is set here,
# every advertisement is refused, `advertising` stays false, the bridge correctly keeps describing
# the host, and the whole chain is silent. The operator sees a daemon that runs and is never
# believed. This is the delivery that ends that.
#
# WE DO NOT WRITE THE FILE. aify-env owns its store: the path resolution, the ACLs, the atomic write
# and the readback are its rules, and reimplementing them here in another language would give two
# answers to one question that agree only until one is fixed. `aify-env credential set` is the
# public way in, and it is the ONLY way this script touches the store.
#
# THE KEY TRAVELS ON STDIN, never argv -- every process on the host can read a command line for as
# long as the command runs -- and never in this script's output. What comes back is the REFERENCE,
# which is not a secret: it is the name the registry carries so the daemon can find the file again.
#
# NO CLAIM OF ATOMICITY ACROSS TIERS. `.env`, the client configs and this carrier are three separate
# writes and one of them can fail after another has succeeded. What this prints is a RECEIPT of what
# actually converged, so a partial state is visible rather than assumed away.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SERVICE_NAME="${AIFY_SERVICE_NAME:-aify-comms}"

#: Not an error. A host that never installed aify-env is a host where the bridge still describes
#: everything itself, which is a supported configuration -- and refusing the whole install over an
#: absent optional tier would be the installer deciding an operator's architecture for them.
if ! command -v aify-env >/dev/null 2>&1; then
  echo "aify-env is not installed, so no credential was stored for it." >&2
  echo "That is fine unless you are delegating spawns: without a stored key, aify-env cannot" >&2
  echo "advertise to a service that requires one, and this bridge keeps describing the host." >&2
  exit 0
fi

key="$(cat)"
if [ -z "$key" ]; then
  echo "ERROR: no key arrived on stdin." >&2
  exit 1
fi

# STDIN, and a here-string would put the key in a temp file on some shells. A pipe keeps it in
# memory and out of the filesystem.
ref=""
if ! ref="$(printf '%s' "$key" | aify-env credential set --service "$SERVICE_NAME" --stdin 2>&1)"; then
  # `ref` holds the command's OWN diagnostic, which names the problem and never the value. It is
  # passed through unchanged rather than reworded into something that might quote what it refused.
  echo "ERROR: aify-env refused the credential: $ref" >&2
  exit 1
fi
ref="$(printf '%s' "$ref" | tr -d '\r' | tail -n1)"
if [ -z "$ref" ]; then
  echo "ERROR: aify-env stored the credential but named no reference, so nothing can find it." >&2
  exit 1
fi

# READ IT BACK THROUGH THE PUBLIC PATH before saying it converged. `credential set` verifies its own
# write, but this is the tier boundary: what matters here is that the DAEMON reports the credential
# healthy, not that the command believed itself.
if ! aify-env credential status --service "$SERVICE_NAME" >/dev/null 2>&1; then
  echo "ERROR: the credential was written and aify-env reports it as faulted." >&2
  echo "       Run: aify-env credential status --service $SERVICE_NAME" >&2
  exit 1
fi

printf '%s\n' "$ref"
