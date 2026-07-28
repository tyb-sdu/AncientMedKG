#!/usr/bin/env bash
set -euo pipefail

ROOT="/data2/lxj/projects/tcm-burn-rag"
OCR_DIR="${ROOT}/ancient_ocr"
RAW_DIR="${ROOT}/corpus/ancient_pdf/raw_flat"
READY_CORPUS="${OCR_DIR}/ready_corpus"
READY_DATA="${OCR_DIR}/ready_data"
OUTPUT_DIR="${OCR_DIR}/output"
MODEL_HOME="${ROOT}/models/ocr"
PYTHON="${ROOT}/.conda-ancient-ocr/bin/python"
CLI="${OCR_DIR}/ancient_cli.py"
LOG_DIR="${OCR_DIR}/logs"

mkdir -p "${READY_CORPUS}" "${READY_DATA}" "${OUTPUT_DIR}" "${LOG_DIR}"
ln -sfn "${RAW_DIR}/医学心悟_公开扫描版.pdf" \
    "${READY_CORPUS}/医学心悟_公开扫描版.pdf"
ln -sfn "${RAW_DIR}/古今医统大全_SSID_卷78-79.pdf" \
    "${READY_CORPUS}/古今医统大全_SSID_卷78-79.pdf"

"${PYTHON}" "${CLI}" \
    --corpus-dir "${READY_CORPUS}" \
    --data-dir "${READY_DATA}" \
    --output-dir "${OUTPUT_DIR}" \
    --model-home "${MODEL_HOME}" \
    inventory

"${PYTHON}" "${CLI}" \
    --corpus-dir "${READY_CORPUS}" \
    --data-dir "${READY_DATA}" \
    --output-dir "${OUTPUT_DIR}" \
    --model-home "${MODEL_HOME}" \
    run --device gpu:0 --shard-index 0 --shard-count 2 \
    >"${LOG_DIR}/ready_worker_0.log" 2>&1 &
worker_0=$!

"${PYTHON}" "${CLI}" \
    --corpus-dir "${READY_CORPUS}" \
    --data-dir "${READY_DATA}" \
    --output-dir "${OUTPUT_DIR}" \
    --model-home "${MODEL_HOME}" \
    run --device gpu:1 --shard-index 1 --shard-count 2 \
    >"${LOG_DIR}/ready_worker_1.log" 2>&1 &
worker_1=$!

status_0=0
status_1=0
wait "${worker_0}" || status_0=$?
wait "${worker_1}" || status_1=$?

if [[ "${status_0}" -ne 0 || "${status_1}" -ne 0 ]]; then
    printf 'ready OCR failed: worker_0=%s worker_1=%s\n' \
        "${status_0}" "${status_1}" >&2
    exit 1
fi

date -Iseconds >"${OCR_DIR}/state/ready_ocr_complete.txt"
