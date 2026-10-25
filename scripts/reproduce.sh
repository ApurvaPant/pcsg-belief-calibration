#!/usr/bin/env bash
# Reproduce EVERY paper table/figure + analysis_v2 outputs from the run folders with
# ZERO API calls, then SHA-256 check against results/MANIFEST.json. (Linux/macOS/Git Bash)
#   bash scripts/reproduce.sh
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
PY="$REPO/backend/.venv/Scripts/python.exe"
[ -x "$PY" ] || PY="$REPO/backend/.venv/bin/python"
export PCSG_OFFLINE=1          # any non-mock LLM call raises
export PYTHONPATH="."
RUN="main_20261015_000553"
cd "$REPO/backend"

echo "== T1-T4 / F1-F3 (make_figures, offline) =="
"$PY" "$REPO/scripts/make_figures.py" --run-id "$RUN"

echo "== analysis_v2 (STATS, TEMPLATES, EVIDENCE_V2, HUMAN_AUDIT) =="
for s in sig_recalib.py templates.py evidence_v2.py human_audit.py; do
  echo "  -> $s"
  "$PY" "$REPO/scripts/analysis_v2/$s"
done
# ABSTAIN_SC.md is a static skip-with-reason (item 4); nothing to recompute.

echo "== SHA-256 manifest check =="
cd "$REPO"
"$PY" "$REPO/scripts/make_manifest.py" --check
echo "REPRODUCE: OK (zero API calls)"
