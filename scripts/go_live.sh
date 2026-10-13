#!/usr/bin/env bash
# go_live.sh — turns live calls on and runs the pilot. Run from the repo root
# AFTER the provider + key are set in backend/.env. On a FREE-TIER provider
# (NVIDIA build, experiment.free_tier: true) it skips price-fill and projects in
# calls / tokens / hours at the configured rate limit instead of dollars. It runs
# a 5-persona DEV pilot (+3 LongMemEval) into results/pilot/, then STOPS.
#
#   bash scripts/go_live.sh
#
# It never launches the full run itself (see the printed command / run_queue.sh).
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO/backend"
PY="./.venv/Scripts/python.exe"
[ -x "$PY" ] || PY="./.venv/bin/python"

echo "=== go_live: live calls permitted (offline guard off) ==="
unset PCSG_OFFLINE || true
export PYTHONPATH=.

PROVIDER="$($PY -c 'from app.config import settings;print(settings.llm.provider)')"
FREE_TIER="$($PY -c 'from app.config import settings;print(settings.experiment.free_tier)')"
echo "provider = $PROVIDER | free_tier = $FREE_TIER"
if [ "$PROVIDER" = "mock" ] || [ "$PROVIDER" = "oracle_mock" ]; then
  echo "ERROR: provider is $PROVIDER; set a real provider + key in backend/.env first." >&2
  exit 1
fi

if [ "$FREE_TIER" = "True" ]; then
  echo "=== STEP 1: free tier — skipping fill_prices.py (no dollar cost) ==="
else
  echo "=== STEP 1: fill live prices ==="
  "$PY" ../scripts/fill_prices.py
fi

echo "=== STEP 2: verify each configured model is served as requested ==="
"$PY" - <<'PYEOF'
from app import llm
from app.config import settings
bad = []
for role, model in settings.llm.models.items():
    if not model or model == "mock" or model.startswith("sentence-transformers/"):
        continue
    r = llm.complete([{"role": "user", "content": "ping"}], model=model, max_tokens=1)
    served = r.get("model")
    tag = "ok" if served == model else "MISMATCH"
    print(f"  {role:18s} requested={model:34s} served={served} [{tag}]")
    if served != model:
        bad.append((role, model, served))
if bad:
    raise SystemExit(f"ERROR: served≠requested for {bad}; aborting before pilot.")
print("all models served as requested.")
PYEOF

echo "=== STEP 3: 5-persona DEV live pilot (+3 LongMemEval) -> results/pilot/ ==="
START=$(date +%s)
"$PY" -m pcsg.run experiments/all.yaml --live --split dev --limit 5 --lme 3 --outdir pilot
END=$(date +%s)
ELAPSED=$((END - START))

echo "=== STEP 4: project the full run in calls / tokens / hours ==="
PILOT_RUN="$(ls -t ../results/pilot | head -1)"
ELAPSED="$ELAPSED" PILOT_RUN="$PILOT_RUN" "$PY" - <<'PYEOF'
import json, os, pathlib, yaml
from app.config import settings
repo = pathlib.Path("..").resolve()
run = os.environ["PILOT_RUN"]; elapsed = float(os.environ["ELAPSED"])
cost = json.loads((repo / "results" / "pilot" / run / "cost.json").read_text())
spec = yaml.safe_load((repo / "experiments" / "all.yaml").read_text())
n_full = int(spec.get("n_personas", 100)); rpm = int(spec.get("rate_limit_per_min", 35))
pilot_n = 5
calls, toks = cost.get("calls", 0), cost.get("prompt_tokens", 0) + cost.get("completion_tokens", 0) + cost.get("reasoning_tokens", 0)
per_calls, per_toks = calls / pilot_n, toks / pilot_n
proj_calls = per_calls * n_full
proj_toks = per_toks * n_full
# wall time is gated by the shared rate limit: total calls / rpm.
proj_hours = proj_calls / rpm / 60.0
print(f"  pilot: {pilot_n} personas — {calls:.0f} calls, {toks:,} tokens, {elapsed:.0f}s")
print(f"  per persona: {per_calls:.0f} calls, {per_toks:,.0f} tokens")
print(f"  PROJECTED full persona run (n={n_full}): ~{proj_calls:,.0f} calls, ~{proj_toks:,.0f} tokens")
print(f"    time at {rpm} RPM (rate-limited): ~{proj_hours:.1f} h")
print(f"  token_budget = {settings.experiment.token_budget:,}  ->  "
      f"{'WITHIN BUDGET' if proj_toks <= settings.experiment.token_budget else 'OVER BUDGET — raise token_budget or shrink n_personas'}")
print("  (LongMemEval 108 Q + cost sweep are additional — see docs/NVIDIA_SWITCH.md projection.)")
PYEOF

echo
echo "=== PILOT COMPLETE — review the projection. go_live STOPS here. ==="
echo "Launch the overnight QUEUE (main run -> figures -> model2 -> ablation -> figures):"
echo "    pwsh -File scripts/run_queue.ps1          # Windows"
echo "    bash  scripts/run_queue.sh                # bash"
echo "Or launch ONLY the main run:"
echo "    cd backend && PYTHONPATH=. $PY -m pcsg.run experiments/all.yaml --live"
echo "    # resume:  --resume <run_id>   (results/runs/<run_id>/)"
echo "Progress: results/QUEUE_STATUS.md"
