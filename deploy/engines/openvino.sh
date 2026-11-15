#!/usr/bin/env bash
# OpenVINO Model Server for the engine comparison (T30): the generator on 8000, the judge on 8001.
# NOT VERIFIED on this laptop: see tmp/T30-comparaison-moteurs.md before using it.
#
#   openvino.sh start gpu|npu
#   openvino.sh stop
#
# The models are OpenVINO IR in INT4, one folder each in $OV_MODELS (default
# ~/.cache/skoolathome/openvino), converted once with optimum-cli (see T30). For the NPU, the
# conversion must be symmetric and per channel (--sym --group-size -1), and the host needs the
# Intel NPU driver (/dev/accel/accel0).
set -euo pipefail

models=${OV_MODELS:-${XDG_CACHE_HOME:-$HOME/.cache}/skoolathome/openvino}
image=${OVMS_IMAGE:-openvino/model_server:latest-gpu}

run() {  # name port folder device
    local devices=(--device /dev/dri --group-add "$(stat -c %g /dev/dri/render* | head -1)")
    [[ $4 == NPU ]] && devices+=(--device /dev/accel --group-add "$(stat -c %g /dev/accel/accel* | head -1)")
    docker run -d --rm --name "skoolathome-ovms-$1" -p "127.0.0.1:$2:$2" "${devices[@]}" \
        -v "$models:/models:ro" "$image" \
        --rest_port "$2" --model_name "$3" --model_path "/models/$3" \
        --task text_generation --target_device "$4" >/dev/null
}

case ${1:-} in
start)
    case ${2:-} in
    gpu) device=GPU ;;
    npu) device=NPU ;;
    *) echo "usage : $0 start gpu|npu" >&2; exit 1 ;;
    esac
    "$0" stop
    run generator 8000 ministral-3-8b-instruct-int4 "$device"
    run judge 8001 qwen3.5-4b-int4 "$device"
    echo "OpenVINO ($device) démarré : docker logs -f skoolathome-ovms-generator"
    ;;
stop)
    docker rm -f skoolathome-ovms-generator skoolathome-ovms-judge >/dev/null 2>&1 || true
    ;;
*)
    sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'
    exit 1
    ;;
esac
