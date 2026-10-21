#!/bin/sh
# The gate's claude launcher: aify-env starts this as it would a real Claude worker (page 2's stand-in).
HARNESS_WRAPPER_VERSION="gate-stand-in"
exec node /opt/gate/stand-in.mjs "$@"
