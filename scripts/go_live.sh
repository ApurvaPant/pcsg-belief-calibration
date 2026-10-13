#!/usr/bin/env bash
# go_live.sh — the ONLY script that turns paid calls on. Run from the repo root
# AFTER the API key is funded. It unsets the offline guard, fills live prices,
# verifies every configured model is served as requested, runs a 5-persona DEV
# pilot into results/pilot/, projects the full-run cost/time against the budget,
# then STOPS and waits for a human to launch the full run.
#
#   bash scripts/go_live.sh
#
# It never launches the full 100-persona run itself (see the printed command).
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO/backend"
PY="./.venv/Scripts/python.exe"
[ -x "$PY" ] || PY="./.venv/bin/python"

echo "=== go_live: DISABLING offline guard (paid calls now permitted) ==="
unset PCSG_OFFLINE
export PYTHONPATH=.

# Guard: a real provider + a funded key must be configured.
PROVIDER="$($PY -c 'from app.config import settings;print(settings.llm.provider)')"
echo "provider = $PROVIDER"
if [ "$PROVIDER" = "mock" ] || [ "$PROVIDER" = "oracle_mock" ]; then
  echo "ERROR: provider is $PROVIDER; set a real provider + key in backend/.env first." >&2
  exit 1
fi

echo "=== STEP 1: fill live prices from OpenRouter ==="
"$PY" ../scripts/fill_prices.py

echo "=== STEP 2: verify each configured model is served as requested ==="
"$PY" - <<'PYEOF'
from app import llm
from app.config import settings
bad = []
for role, model in settings.llm.models.items():
    if not model or model == "mock":
        continue
    r = llm.complete([{"role": "user", "content": "ping"}], model=model, max_tokens=1)
    served = r.get("model")
    tag = "ok" if served == model else "MISMATCH"
    print(f"  {role:18s} requested={model:28s} served={served} [{tag}]")
    if served != model:
        bad.append((role, model, served))
if bad:
    raise SystemExit(f"ERROR: served≠requested for {bad}; aborting before pilot.")
print("all models served as requested.")
PYEOF

echo "=== STEP 3: 5-persona DEV live pilot -> results/pilot/ ==="
START=$(date +%s)
"$PY" -m pcsg.run experiments/all.yaml --live --split dev --limit 5 --outdir pilot
END=$(date +%s)
ELAPSED=$((END - START))

echo "=== STEP 4: project full-run cost + time vs budget_usd ==="
PILOT_RUN="$(ls -t ../results/pilot | head -1)"
ELAPSED="$ELAPSED" PILOT_RUN="$PILOT_RUN" "$PY" - <<'PYEOF'
import json, os, pathlib
from app.config import settings
repo = pathlib.Path("..").resolve()
run = os.environ["PILOT_RUN"]; elapsed = float(os.environ["ELAPSED"])
cost = json.loads((repo / "results" / "pilot" / run / "cost.json").read_text())
import yaml
spec = yaml.safe_load((repo / "experiments" / "all.yaml").read_text())
n_full = int(spec.get("n_personas", 100)); workers = int(spec.get("workers", 10))
pilot_n = 5
per_usd = cost["usd"] / pilot_n
per_sec = elapsed / pilot_n
budget = settings.experiment.budget_usd
proj_usd = per_usd * n_full
proj_sec_serial = per_sec * n_full
proj_sec_parallel = proj_sec_serial / max(1, workers)
print(f"  pilot: {pilot_n} personas, ${cost['usd']:.4f}, {elapsed:.0f}s ({cost['calls']:.0f} calls)")
print(f"  per persona: ${per_usd:.4f}, {per_sec:.1f}s")
print(f"  PROJECTED full run (n={n_full}): ${proj_usd:.2f}")
print(f"    time: ~{proj_sec_serial/60:.0f} min serial / ~{proj_sec_parallel/60:.0f} min at workers={workers}")
print(f"  budget_usd = ${budget:.2f}  ->  {'WITHIN BUDGET' if proj_usd <= budget else 'OVER BUDGET — raise budget or shrink n_personas'}")
PYEOF

echo
echo "=== PILOT COMPLETE — review the projection above. go_live STOPS here. ==="
echo "To launch the FULL run (not started automatically):"
echo "    cd backend && PYTHONPATH=. $PY -m pcsg.run experiments/all.yaml --live"
echo "    # resume after any interruption:  --resume <run_id>   (results/runs/<run_id>/)"
echo "Then regenerate figures/tables:"
echo "    cd .. && $PY scripts/make_figures.py"
