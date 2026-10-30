#!/usr/bin/env bash
# Reproduce EVERY paper table/figure + analysis_v2 outputs from the run folders with
# ZERO API calls, then SHA-256 check against results/MANIFEST.json. (Linux/macOS/Git Bash)
#   bash scripts/reproduce.sh
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
PY="$REPO/backend/.venv/bin/python"
[ -x "$PY" ] || PY="$REPO/backend/.venv/Scripts/python.exe"
export PCSG_OFFLINE=1          # any non-mock LLM call raises
export PYTHONPATH="."
RUN="main_20261015_000553"
cd "$REPO/backend"

# set -e already aborts on a non-zero exit, but check each step explicitly so the
# failure names the step instead of dying silently.
step() {  # step "<label>" <py-script> [args...]
  local label="$1"; shift
  echo "$label"
  if ! "$PY" "$@"; then
    echo "REPRODUCE: FAILED at '$label' (exit $?)"
    exit 1
  fi
}

step "== T1-T4 / F1-F3 (make_figures, offline) ==" "$REPO/scripts/make_figures.py" --run-id "$RUN"

echo "== analysis_v2 (STATS, TEMPLATES, EVIDENCE_V2, HUMAN_AUDIT) =="
for s in sig_recalib.py templates.py evidence_v2.py human_audit.py; do
  step "  -> $s" "$REPO/scripts/analysis_v2/$s"
done
# ABSTAIN_SC.md is a static skip-with-reason (item 4); nothing to recompute.

echo "== SHA-256 manifest check =="
cd "$REPO"
step "  -> make_manifest.py --check" "$REPO/scripts/make_manifest.py" --check
echo "REPRODUCE: OK (zero API calls)"
