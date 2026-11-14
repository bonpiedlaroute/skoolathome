#!/usr/bin/env bash
# Langfuse on this laptop.
#
#   langfuse.sh init     generate the secrets, once (~/.config/skoolathome/langfuse.env, mode 600)
#   langfuse.sh start    start and wait until the web app answers (first start: a few minutes)
#   langfuse.sh stop     stop the containers; the data stays in the Docker volumes
#   langfuse.sh status   containers and memory used
#   langfuse.sh logs [service]
#
# Stopped, it frees 3 to 4 GB of RAM: start it for the evening run, or to look at the traces.
set -euo pipefail

here=$(cd "$(dirname "$0")" && pwd)
conf_dir=${SKOOLATHOME_SECRETS:-$HOME/.config/skoolathome}
env_file=$conf_dir/langfuse.env
url=http://localhost:3000

compose() {
    docker compose -p skoolathome-langfuse -f "$here/compose.yaml" --env-file "$env_file" "$@"
}

need_init() {
    [[ -f $env_file ]] || { echo "Lance d'abord : $0 init" >&2; exit 1; }
}

case ${1:-} in
init)
    if [[ -f $env_file ]]; then
        echo "$env_file existe déjà : rien à faire." >&2
        exit 1
    fi
    umask 077
    mkdir -p "$conf_dir"
    secret() { openssl rand -hex "${1:-24}"; }
    public_key=pk-lf-$(secret 16)
    secret_key=sk-lf-$(secret 16)
    password=$(secret 12)
    cat > "$env_file" <<EOF
LANGFUSE_POSTGRES_PASSWORD=$(secret)
LANGFUSE_CLICKHOUSE_PASSWORD=$(secret)
LANGFUSE_REDIS_PASSWORD=$(secret)
LANGFUSE_MINIO_PASSWORD=$(secret)
LANGFUSE_SALT=$(secret)
LANGFUSE_ENCRYPTION_KEY=$(secret 32)
LANGFUSE_NEXTAUTH_SECRET=$(secret)
LANGFUSE_PUBLIC_KEY=$public_key
LANGFUSE_SECRET_KEY=$secret_key
LANGFUSE_LOGIN=parent@skoolathome.local
LANGFUSE_PASSWORD=$password
EOF
    # What the Python code reads (skoolathome.agent.tracing).
    printf '{"host": "%s", "public_key": "%s", "secret_key": "%s"}\n' "$url" "$public_key" "$secret_key" \
        > "$conf_dir/langfuse.json"
    echo "Secrets écrits dans $env_file et $conf_dir/langfuse.json (mode 600)."
    echo "Connexion à $url : parent@skoolathome.local, mot de passe dans $env_file (LANGFUSE_PASSWORD)."
    ;;
start)
    need_init
    compose up -d
    printf 'Attente de Langfuse'
    for _ in $(seq 1 120); do
        if curl -fsS "$url/api/public/health" >/dev/null 2>&1; then
            echo; echo "Langfuse prêt : $url"
            exit 0
        fi
        printf '.'; sleep 3
    done
    echo; echo "Langfuse ne répond pas après 6 minutes : $0 logs langfuse-web" >&2
    exit 1
    ;;
stop)
    need_init
    compose stop
    ;;
status)
    need_init
    compose ps --format 'table {{.Service}}\t{{.State}}\t{{.Status}}'
    ids=$(compose ps -q)
    if [[ -n $ids ]]; then
        # shellcheck disable=SC2086
        docker stats --no-stream --format 'table {{.Name}}\t{{.MemUsage}}' $ids
    fi
    ;;
logs)
    need_init
    compose logs -f --tail=100 "${@:2}"
    ;;
*)
    sed -n '2,10p' "$0" | sed 's/^# \{0,1\}//'
    exit 1
    ;;
esac
