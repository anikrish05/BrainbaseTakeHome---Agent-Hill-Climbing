#!/bin/sh
# Canonical demo server: saved Python agent, chat history, and real improvement evidence.
set -eu
cd "$(dirname "$0")"
export PORT=8001
export DATA_DIR="$PWD/.data/demo-walkthrough-20261020"
export BATCH_CANDIDATES=2 BATCH_MAX_RUNS=64 BATCH_TIMEOUT_SECONDS=900 BATCH_MAX_COST_USD=2
export EVAL_CONCURRENCY=6 RUN_CONCURRENCY=2
exec ./run.sh
