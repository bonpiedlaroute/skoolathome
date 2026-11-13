#!/bin/sh
# Starts bunker-relay or bunkerd, depending on BUNKER_ROLE.
#
# The vault's files are written to the volume from Railway variables at every start:
# the variables are the source of truth, so changing one (for instance BUNKER_AGENTS_JSON
# to revoke an agent) and redeploying is enough.
set -eu

case "${BUNKER_ROLE:-}" in
relay)
    # Railway gives the port in $PORT and puts TLS in front: the URI says wss://.
    export BUNKER_RELAY_LISTEN="${BUNKER_RELAY_LISTEN:-0.0.0.0:${PORT:-7777}}"
    exec bunker-relay
    ;;
vault)
    umask 077
    data="${BUNKER_DATA:-/data}"
    mkdir -p "$data"
    cd "$data"

    write_b64() { # variable name, file
        eval "value=\${$1:-}"
        if [ -n "$value" ]; then printf '%s' "$value" | base64 -d > "$2"; fi
    }
    write_b64 BUNKER_SIGNER_SEALED_B64 signer.sealed.json
    write_b64 BUNKER_IDENTITY_SEALED_B64 identity.sealed.json
    for f in signer.sealed.json identity.sealed.json; do
        [ -s "$f" ] || { echo "missing $f: set BUNKER_SIGNER_SEALED_B64 and BUNKER_IDENTITY_SEALED_B64" >&2; exit 2; }
    done

    echo '[{ "signer": "signer.sealed.json", "user": "identity.sealed.json" }]' > identities.json
    # Default-deny: NIP-98 HTTP authentication (kind 27235) only. A few signatures per evening.
    default_policy='{"allowed_methods": ["connect", "get_public_key", "sign_event", "ping"], "allowed_kinds": [27235], "rate_limit": {"per_caller": {"burst": 5, "per_second": 0.1}, "global": {"burst": 10, "per_second": 0.2}}}'
    no_agent='{"agents": []}'
    printf '%s\n' "${BUNKER_POLICY_JSON:-$default_policy}" > policy.json
    printf '%s\n' "${BUNKER_AGENTS_JSON:-$no_agent}" > agents.json
    touch audit.log

    # The journal is a file on the volume. Copy each new line to the output, so Railway's
    # logs show it too: metadata only, never any content.
    tail -n 0 -F audit.log 2>/dev/null | sed -u 's/^/journal: /' &

    bunkerd &
    pid=$!
    # SIGHUP reloads agents.json. Railway stops with SIGTERM, bunkerd flushes its journal on SIGINT.
    trap 'kill -HUP "$pid"' HUP
    trap 'kill -INT "$pid"' TERM INT
    status=0
    while :; do
        wait "$pid" && status=0 || status=$?
        kill -0 "$pid" 2>/dev/null || break
    done
    exit "$status"
    ;;
"")
    if [ "$#" -gt 0 ]; then exec "$@"; fi
    echo "set BUNKER_ROLE to relay or vault" >&2
    exit 2
    ;;
*)
    echo "unknown BUNKER_ROLE: $BUNKER_ROLE (relay or vault)" >&2
    exit 2
    ;;
esac
