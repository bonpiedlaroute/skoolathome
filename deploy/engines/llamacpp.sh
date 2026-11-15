#!/usr/bin/env bash
# llama.cpp servers for the engine comparison (T30): the generator on 8081, the judge on 8082.
#
#   llamacpp.sh start vulkan   the layers on the Intel iGPU (-ngl 99)
#   llamacpp.sh start cpu      the processor only (-ngl 0): the baseline
#   llamacpp.sh stop | status
#
# llama-server: a llama.cpp release built with Vulkan (llama-<n>-bin-ubuntu-vulkan-x64.zip on
# GitHub), in PATH or in LLAMA_SERVER. The same binary runs on the processor with -ngl 0.
# The models: the GGUF files Ollama already downloaded (same weights, same quantization), or
# GENERATOR_GGUF / JUDGE_GGUF. Stop Ollama first (sudo systemctl stop ollama): two engines in
# memory would distort the measures, and the RAM would not hold both.
set -euo pipefail

server=${LLAMA_SERVER:-llama-server}
state=${XDG_RUNTIME_DIR:-/tmp}/skoolathome-llamacpp
logs=${XDG_CACHE_HOME:-$HOME/.cache}/skoolathome
threads=${LLAMA_THREADS:-10}

gguf() {  # the weights file of an Ollama model
    ollama show --modelfile "$1" | awk '/^FROM \//{print $2; exit}'
}

start_one() {  # name port file extra arguments…
    local name=$1 port=$2 file=$3
    shift 3
    if [[ ! -r $file ]]; then
        echo "Fichier illisible : '$file'. Donne-le avec GENERATOR_GGUF ou JUDGE_GGUF." >&2
        exit 1
    fi
    nohup "$server" -m "$file" --host 127.0.0.1 --port "$port" -c 8192 -t "$threads" --jinja "$@" \
        >"$logs/llamacpp-$name.log" 2>&1 &
    echo $! >"$state-$name.pid"
}

wait_ready() {
    for _ in $(seq 1 120); do
        curl -fsS "http://127.0.0.1:$1/health" >/dev/null 2>&1 && return 0
        sleep 2
    done
    echo "Pas prêt sur le port $1 : voir $logs/llamacpp-*.log" >&2
    return 1
}

case ${1:-} in
start)
    case ${2:-} in
    vulkan) layers=99 ;;
    cpu) layers=0 ;;
    *) echo "usage : $0 start vulkan|cpu" >&2; exit 1 ;;
    esac
    "$0" stop
    mkdir -p "$logs"
    start_one generator 8081 "${GENERATOR_GGUF:-$(gguf ministral-3:8b-instruct-2512-q4_K_M)}" -ngl "$layers"
    start_one judge 8082 "${JUDGE_GGUF:-$(gguf qwen3.5:4b-q4_K_M)}" -ngl "$layers" --reasoning-budget 0
    wait_ready 8081 && wait_ready 8082
    echo "llama.cpp ($2) prêt : générateur sur 8081, correcteur sur 8082. Journaux : $logs"
    ;;
stop)
    for name in generator judge; do
        if [[ -f $state-$name.pid ]]; then
            kill "$(cat "$state-$name.pid")" 2>/dev/null || true
            rm -f "$state-$name.pid"
        fi
    done
    ;;
status)
    for name in generator judge; do
        if [[ -f $state-$name.pid ]] && kill -0 "$(cat "$state-$name.pid")" 2>/dev/null; then
            echo "$name : en marche (pid $(cat "$state-$name.pid"))"
        else
            echo "$name : arrêté"
        fi
    done
    ;;
*)
    sed -n '2,13p' "$0" | sed 's/^# \{0,1\}//'
    exit 1
    ;;
esac
