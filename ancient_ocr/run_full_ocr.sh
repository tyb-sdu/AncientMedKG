#!/usr/bin/env bash
set -euo pipefail

ROOT="/data2/lxj/projects/tcm-burn-rag"
OCR_DIR="${ROOT}/ancient_ocr"
CORPUS_DIR="${ROOT}/corpus/ancient_pdf/raw_flat"
DATA_DIR="${OCR_DIR}/data"
OUTPUT_DIR="${OCR_DIR}/output"
MODEL_HOME="${ROOT}/models/ocr"
PYTHON="${ROOT}/.conda-ancient-ocr/bin/python"
CLI="${OCR_DIR}/ancient_cli.py"
LOG_DIR="${OCR_DIR}/logs"

mkdir -p "${DATA_DIR}" "${OUTPUT_DIR}" "${LOG_DIR}" "${OCR_DIR}/state"
exec 9>"${OCR_DIR}/state/full_ocr.lock"
if ! flock -n 9; then
    echo "full ancient OCR is already running"
    exit 0
fi

"${PYTHON}" "${CLI}" \
    --corpus-dir "${CORPUS_DIR}" \
    --data-dir "${DATA_DIR}" \
    --output-dir "${OUTPUT_DIR}" \
    --model-home "${MODEL_HOME}" \
    inventory

"${PYTHON}" "${CLI}" \
    --corpus-dir "${CORPUS_DIR}" \
    --data-dir "${DATA_DIR}" \
    --output-dir "${OUTPUT_DIR}" \
    --model-home "${MODEL_HOME}" \
    run --device gpu:0 --shard-index 0 --shard-count 2 \
    >"${LOG_DIR}/full_worker_0.log" 2>&1 &
worker_0=$!

"${PYTHON}" "${CLI}" \
    --corpus-dir "${CORPUS_DIR}" \
    --data-dir "${DATA_DIR}" \
    --output-dir "${OUTPUT_DIR}" \
    --model-home "${MODEL_HOME}" \
    run --device gpu:1 --shard-index 1 --shard-count 2 \
    >"${LOG_DIR}/full_worker_1.log" 2>&1 &
worker_1=$!

status_0=0
status_1=0
wait "${worker_0}" || status_0=$?
wait "${worker_1}" || status_1=$?

if [[ "${status_0}" -ne 0 || "${status_1}" -ne 0 ]]; then
    printf 'full OCR failed: worker_0=%s worker_1=%s\n' \
        "${status_0}" "${status_1}" >&2
    exit 1
fi

"${PYTHON}" "${CLI}" \
    --corpus-dir "${CORPUS_DIR}" \
    --data-dir "${DATA_DIR}" \
    --output-dir "${OUTPUT_DIR}" \
    --model-home "${MODEL_HOME}" \
    finalize

"${PYTHON}" "${CLI}" \
    --corpus-dir "${CORPUS_DIR}" \
    --data-dir "${DATA_DIR}" \
    --output-dir "${OUTPUT_DIR}" \
    --model-home "${MODEL_HOME}" \
    doctor --deep

date -Iseconds >"${OCR_DIR}/state/full_ocr_complete.txt"
