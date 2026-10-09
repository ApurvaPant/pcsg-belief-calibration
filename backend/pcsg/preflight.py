"""Preflight (STEP 4): python -m pcsg.preflight [experiments/<name>.yaml] [--live]

Runs NOTHING else. Checks: API key present; one 1-token call per configured model
role (LIVE only — skipped in dry-run); credit/billing errors reported clearly;
datasets present at the pinned revision; free disk space; and a PROJECTED live cost
for the experiment from estimated token counts x the price table (flagging any
price still left as a TODO default).
"""
from __future__ import annotations

import argparse
import shutil
import sys

ROLES = ("chat", "classifier", "extractor", "judge")


def _probe(role):
    from app import llm
    m = llm.resolve_model(role)
    try:
        llm.complete([{"role": "user", "content": "ok"}], model=m, max_tokens=1)
        return f"  {role:10} {m:28} OK"
    except Exception as e:  # noqa: BLE001
        msg = str(e)
        hint = ""
        if "402" in msg or "credit" in msg.lower() or "billing" in msg.lower() or "quota" in msg.lower():
            hint = "  <-- CREDIT/BILLING: add funds / enable billing on this project"
        return f"  {role:10} {m:28} FAIL {type(e).__name__}: {msg[:90]}{hint}"


def _project_cost(spec, settings):
    n = int(spec.get("n_personas", 100))
    turns = 25
    # calls/turn: salience(1) + frame(1) + fill(1) + self-consistency(3) + chat(1) = 7
    calls = n * turns * 7
    # ~250 prompt + ~80 completion tokens per call (estimate)
    in_tok, out_tok = calls * 250, calls * 80
    ex = settings.experiment
    extractor = settings.llm.models.get("extractor", "default")
    usd = ex.cost_usd(extractor, in_tok, out_tok)
    is_default = extractor not in ex.prices_per_mtok
    return {"calls": calls, "in_tok": in_tok, "out_tok": out_tok, "usd": usd, "price_is_TODO": is_default}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("spec", nargs="?", default="experiments/all.yaml")
    ap.add_argument("--live", action="store_true")
    args = ap.parse_args(argv)
    import os, pathlib, yaml
    repo = pathlib.Path(__file__).resolve().parent.parent.parent
    spec = yaml.safe_load((repo / args.spec).read_text(encoding="utf-8"))
    if not args.live:
        os.environ["LLM_PROVIDER"] = "oracle_mock"
    from app.config import settings
    if not args.live:
        settings.llm.provider = "oracle_mock"

    print(f"PREFLIGHT ({'LIVE' if args.live else 'DRY-RUN'}) — {args.spec}")
    print(f"provider={settings.llm.provider} base_url={settings.llm.base_url}")
    print(f"API key present: {'yes' if settings.llm.api_key else 'NO'}")

    print("Model role probes:")
    if args.live:
        for r in ROLES:
            print(_probe(r))
    else:
        print("  (dry-run: model probes skipped — oracle_mock, no network)")

    print("Datasets:")
    try:
        from pcsg.longmemeval import verify, CACHE
        print(f"  LongMemEval cache: {'present' if CACHE.exists() else 'absent'} ({CACHE})")
        ok, msg = verify()
        print(f"  {'OK ' if ok else 'WARN'} {msg}")
    except Exception as e:  # noqa: BLE001
        print(f"  WARN LongMemEval: {type(e).__name__}: {str(e)[:80]}")

    du = shutil.disk_usage(str(repo))
    print(f"Disk free: {du.free/1e9:.1f} GB")

    pc = _project_cost(spec, settings)
    print(f"Projected LIVE cost: ~${pc['usd']:.2f}  ({pc['calls']} calls, ~{pc['in_tok']//1000}k in / {pc['out_tok']//1000}k out tok)")
    if pc["price_is_TODO"]:
        print("  TODO: fill real per-Mtok prices for your model in config.yaml experiment.prices_per_mtok")
    print(f"  budget_usd = {settings.experiment.budget_usd}  ->", "WITHIN" if pc["usd"] <= settings.experiment.budget_usd else "EXCEEDS (shrink --n_personas or raise budget)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
