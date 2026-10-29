"""results/MANIFEST.json — SHA-256 of every reproducibility input + output, plus run
metadata. Two modes:
  python scripts/make_manifest.py            # (re)generate results/MANIFEST.json
  python scripts/make_manifest.py --check    # re-hash and compare to the committed file
                                             # hard-fail on input/CSV/MD/JSON drift; warn on PNG
OFFLINE: pure hashing, no API.
"""
from __future__ import annotations
import hashlib, json, pathlib, subprocess, sys

REPO = pathlib.Path(__file__).resolve().parents[1]
RUN = REPO / "results" / "runs" / "main_20261015_000553"
MANIFEST = REPO / "results" / "MANIFEST.json"

INPUTS = [
    "results/runs/main_20261015_000553/persona_run.jsonl",
    "results/runs/main_20261015_000553/labelled.jsonl",
    "results/runs/main_20261015_000553/truth.json",
    "results/runs/main_20261015_000553/calibration.json",
    "results/runs/main_20261015_000553/config.json",
    "results/runs/main_20261015_000553/cost.json",
    "results/runs/main_20261015_000553/cost_curve.json",
    "results/runs/main_20261015_000553/qa_systems.json",
    "results/runs/main_20261015_000553/persona_users.json",
    "results/runs/main_20261015_000553/baselines.json",
    "results/runs/main_20261015_000553/abstention.json",
    "backend/config.yaml",
    "backend/app/extraction.py",
    "results/analysis/error_audit_v2_raj.csv",
    "results/analysis/error_audit_v2_key.csv",
    "results/lme/lme_gptoss_20261026b/judge_check_lme_human.csv",
]
OUTPUT_GLOBS = ["results/tables/*", "results/figures/F1*", "results/figures/F2*",
                "results/figures/F3*", "results/analysis_v2/*.md",
                "results/analysis_v2/*.csv", "results/analysis_v2/*.json",
                "results/analysis_v2/*.png",
                "results/lme/lme_gptoss_20261026b/JUDGE_CHECK.md"]


def sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(65536), b""):
            h.update(b)
    return h.hexdigest()


def _git(*a):
    try:
        return subprocess.check_output(["git", *a], cwd=REPO, text=True).strip()
    except Exception:
        return None


def collect():
    inp = {}
    for rel in INPUTS:
        p = REPO / rel
        if p.exists():
            inp[rel] = sha(p)
    out = {}
    for g in OUTPUT_GLOBS:
        for p in sorted(REPO.glob(g)):
            if p.is_file():
                out[str(p.relative_to(REPO)).replace("\\", "/")] = sha(p)
    cfg = json.loads((RUN / "config.json").read_text()) if (RUN / "config.json").exists() else {}
    cost = json.loads((RUN / "cost.json").read_text()) if (RUN / "cost.json").exists() else {}
    meta = {
        "run_id": cfg.get("run_id"), "run_git_sha": cfg.get("git_sha"),
        "repo_git_commit": _git("rev-parse", "HEAD"),
        "models": cfg.get("models"), "seed": cfg.get("seed"), "n_personas": cfg.get("n_personas"),
        "temperature": cfg.get("temperature"), "max_tokens": cfg.get("max_tokens"),
        "prompt_version": cfg.get("prompt_version"), "prompt_hashes": cfg.get("prompt_hashes"),
        "self_consistency_k": 3, "rate_limit_per_min": 35,
        "longmemeval_revision": "98d7416c24c778c2fee6e6f3006e7a073259d48f",
        "cost_meter": {"calls": cost.get("calls"), "prompt_tokens": cost.get("prompt_tokens"),
                       "completion_tokens": cost.get("completion_tokens"),
                       "reasoning_tokens": cost.get("reasoning_tokens")},
        "cost_meter_note": ("cost.json reflects the meter state in the main run folder; the headline "
                            "'22.5M tokens / 12,787 calls' is the tier1_replay_calibration total from "
                            "results/QUEUE_STATUS.md and is NOT equal to this cost.json (see "
                            "docs/PAPER_NUMBERS.md)."),
    }
    return {"schema": 1, "inputs": inp, "outputs": out, "metadata": meta}


def generate():
    m = collect()
    MANIFEST.write_text(json.dumps(m, indent=2), encoding="utf-8")
    print(f"[manifest] wrote {MANIFEST.relative_to(REPO)} "
          f"({len(m['inputs'])} inputs, {len(m['outputs'])} outputs)")


def check():
    if not MANIFEST.exists():
        print("[manifest] no MANIFEST.json yet — generating it (first reproduce run).")
        generate()
        return 0
    want = json.loads(MANIFEST.read_text())
    now = collect()
    hard, soft = [], []
    for section in ("inputs", "outputs"):
        for path, h in want[section].items():
            cur = now[section].get(path)
            if cur != h:
                (soft if path.endswith(".png") else hard).append(
                    f"{section}:{path} expected {h[:12]} got {(cur or 'MISSING')[:12]}")
    for s in soft:
        print(f"[manifest] WARN (png, backend-dependent): {s}")
    if hard:
        for s in hard:
            print(f"[manifest] CHECK FAIL: {s}")
        return 1
    print(f"[manifest] CHECK OK: {len(want['inputs'])} inputs + "
          f"{len(want['outputs'])} outputs match (PNG warnings: {len(soft)}).")
    return 0


if __name__ == "__main__":
    sys.exit(check() if "--check" in sys.argv else (generate() or 0))
