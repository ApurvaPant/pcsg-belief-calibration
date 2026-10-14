#!/usr/bin/env bash
# run_queue.sh — overnight priority queue (bash mirror of run_queue.ps1).
# Stages in priority order; resumable (per-stage markers + run.py --resume);
# graceful pause via results/PAUSE (the in-progress persona finishes); writes
# results/QUEUE_STATUS.md. Survives network drops (backoff in llm.py).
#
#   bash scripts/run_queue.sh [PERSONAS] [RUN_ID] [LME] [--optional]
#   pause:    touch results/PAUSE
#   resume:   re-run with the same RUN_ID
#   progress: cat results/QUEUE_STATUS.md
set -uo pipefail
PERSONAS="${1:-60}"; RUN_ID="${2:-main_$(date +%Y%m%d_%H%M%S)}"; LME="${3:-108}"; OPT="${4:-}"
REPO="$(cd "$(dirname "$0")/.." && pwd)"; BACKEND="$REPO/backend"; RESULTS="$REPO/results"
PY="$BACKEND/.venv/Scripts/python.exe"; [ -x "$PY" ] || PY="$BACKEND/.venv/bin/python"
QDIR="$RESULTS/runs/$RUN_ID/queue"; mkdir -p "$QDIR"; STATUS="$RESULTS/QUEUE_STATUS.md"; PAUSE="$RESULTS/PAUSE"
T0=$(date +%s)
trap 'touch "$PAUSE"; echo "[queue] Ctrl+C -> PAUSE requested; current persona will finish."' INT

status() {  # $1 stage, $2 state
  local prog="$RESULTS/runs/$RUN_ID/progress.json" active=$(( ($(date +%s)-T0)/60 ))
  { echo "# QUEUE_STATUS — run $RUN_ID"; echo; echo "updated: $(date -Is) | active min: $active | personas: $PERSONAS";
    echo "current stage: **$1** — $2"; echo;
    [ -f "$prog" ] && "$PY" -c "import json,sys;d=json.load(open(sys.argv[1]));print(f\"replay {d['done']}/{d['total']} tokens {d['spent_tokens']} calls {d['calls']} 429 {d['http_429']}\")" "$prog" 2>/dev/null;
  } > "$STATUS"
}

LAST="$RESULTS/runs/$RUN_ID/last_stage.json"
run_stage() {  # $1 name ; rest: args to pcsg.run  (or: name --figures) ; optional last arg "NOCALLS"
  local name="$1"; shift; local marker="$QDIR/$name.done"; local expect_calls=1
  [ "${*: -1}" = "NOCALLS" ] && { set -- "${@:1:$(($#-1))}"; expect_calls=0; }
  [ -f "$marker" ] && { echo "[queue] $name done — skip"; return; }
  [ -f "$PAUSE" ] && { status "$name" "PAUSED before start"; echo "[queue] PAUSED"; exit 0; }
  echo "[queue] STAGE $name"
  if [ "$1" = "--figures" ]; then
    echo "[queue]   cmd: $PY scripts/make_figures.py --run-id $RUN_ID --require-live"
    status "$name" "running"; ( cd "$REPO" && PYTHONUTF8=1 PYTHONIOENCODING=utf-8 "$PY" scripts/make_figures.py --run-id "$RUN_ID" --require-live ); rc=$?
  else
    echo "[queue]   cmd: $PY -m pcsg.run $*"
    status "$name" "running"; ( cd "$BACKEND" && PYTHONPATH=. PYTHONUTF8=1 PYTHONIOENCODING=utf-8 "$PY" -m pcsg.run "$@" ); rc=$?
  fi
  if [ -f "$PAUSE" ]; then status "$name" "PAUSED"; echo "[queue] PAUSED — remove results/PAUSE and re-run with RUN_ID=$RUN_ID"; exit 0; fi
  if [ $rc -ne 0 ]; then status "$name" "ERROR rc=$rc"; echo "[queue] $name FAILED rc=$rc — STOPPING"; exit $rc; fi
  # Guard (pcsg.run stages): verify it ran live for THIS run id and made calls.
  if [ "$1" != "--figures" ]; then
    local ok; ok=$("$PY" -c "import json,sys;d=json.load(open(sys.argv[1]));print(d.get('mode'),d.get('run_id'),d.get('model_calls'))" "$LAST" 2>/dev/null)
    local m=$(echo "$ok" | awk '{print $1}'); local rid=$(echo "$ok" | awk '{print $2}'); local calls=$(echo "$ok" | awk '{print $3}')
    [ "$m" = "live" ] || { echo "[queue] GUARD FAIL: $name mode=$m (expected live)"; exit 11; }
    [ "$rid" = "$RUN_ID" ] || { echo "[queue] GUARD FAIL: $name run_id=$rid != $RUN_ID"; exit 12; }
    if [ "$expect_calls" = "1" ] && [ "${calls:-0}" -le 0 ]; then echo "[queue] GUARD FAIL: $name made 0 model calls"; exit 13; fi
    echo "[queue] $name DONE mode=$m calls=$calls"
  fi
  touch "$marker"; status "$name" "done"; echo "[queue] $name done"
}

echo "[queue] run_id=$RUN_ID personas=$PERSONAS (live; provider from config.yaml)"
run_stage tier1_replay_calibration experiments/all.yaml --live --n "$PERSONAS" --run-id "$RUN_ID" --stages replay,calibration
run_stage tier1_figures --figures
run_stage tier2_qa experiments/all.yaml --live --resume "$RUN_ID" --n "$PERSONAS" --stages replay,qa
run_stage tier2_figures --figures
run_stage tier3_longmemeval experiments/all.yaml --live --resume "$RUN_ID" --n "$PERSONAS" --lme "$LME" --stages replay,lme
run_stage cost_sweep experiments/all.yaml --live --resume "$RUN_ID" --n "$PERSONAS" --stages replay,cost NOCALLS
run_stage final_figures --figures
if [ "$OPT" = "--optional" ]; then
  run_stage model2_gptoss experiments/model2.yaml --live --run-id "model2_$RUN_ID" --stages replay,calibration
  run_stage effort_ablation experiments/effort_ablation.yaml --live --run-id "ablate_$RUN_ID" --stages replay,calibration
  run_stage final_figures_opt --figures
fi
status "ALL" "complete"; echo "[queue] ALL STAGES COMPLETE"
