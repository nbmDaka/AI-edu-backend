#!/usr/bin/env bash
set -euo pipefail
remaining=$(( AVATAR_EXPIRES_AT - $(date +%s) ))
if (( remaining <= 0 )); then exit 0; fi
ssh -NT -i /run/secrets/bridge-key -o BatchMode=yes -o ExitOnForwardFailure=yes \
  -o StrictHostKeyChecking=yes -o UserKnownHostsFile=/run/secrets/known-hosts \
  -o ServerAliveInterval=10 -o ServerAliveCountMax=2 \
  -L127.0.0.1:8012:127.0.0.1:8012 "a2f_bridge@${A2F_SSH_HOST}" &
tunnel=$!
node /app/gateway.cjs &
gateway=$!
(sleep "$remaining") &
expiry=$!
trap 'kill "$tunnel" "$gateway" "$expiry" 2>/dev/null || true' EXIT INT TERM
wait -n "$tunnel" "$gateway" "$expiry"
