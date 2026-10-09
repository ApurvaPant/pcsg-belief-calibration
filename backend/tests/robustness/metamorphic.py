"""S3 metamorphic tests (real provider).

Properties that must hold between related inputs without a single gold answer:
  MT-1 paraphrase invariance   — 10 fact families x 5 phrasings -> same signature
  MT-6 idempotency             — same message x5 -> one node, conf non-decreasing <1
  MT-7 negation discrimination — affirm / negate / cease -> three different outcomes
  MT-8 subject discrimination  — only "my name is X" touches identity

Each variant runs in a FRESH user (via /debug/reset + register). Per-family
consistency = fraction of variant pairs with identical active-node signatures.
Results are written to results/consistency/<run_id>/.

Run (from backend/):
  PCSG_TEST_MODE=1 PYTHONPATH=. ./.venv/Scripts/python tests/robustness/metamorphic.py --provider real
"""
from __future__ import annotations

import argparse
import itertools
import json
import os
import pathlib
import tempfile
import time
import uuid

# ── MT-1 paraphrase families: each a list of 5 phrasings of ONE fact ───────── #
MT1_FAMILIES = {
    "diet_vegetarian": [
        "i'm vegetarian", "i am a vegetarian", "i eat only vegetarian food",
        "i don't eat meat, i'm veg", "being vegetarian is important to me",
    ],
    "city_residence": [
        "i live in pune", "i'm based in pune", "pune is where i live",
        "i reside in pune", "my home is in pune",
    ],
    "occupation": [
        "i work as a data analyst", "i'm a data analyst", "my job is data analyst",
        "i do data analysis for work", "professionally i'm a data analyst",
    ],
    "language_skill": [
        "i speak french", "i know french", "i'm fluent in french",
        "i can speak french", "french is a language i speak",
    ],
    "pet_named": [
        "my dog is named rex", "my dog's name is rex", "i have a dog called rex",
        "rex is my dog", "i've got a dog, his name is rex",
    ],
    "liked_food": [
        "i love ramen", "i really like ramen", "ramen is my favourite food",
        "i'm a big fan of ramen", "i enjoy eating ramen",
    ],
    "disliked_activity": [
        "i hate jogging", "i can't stand jogging", "i really dislike jogging",
        "jogging is something i hate", "i'm not a fan of jogging at all",
    ],
    "career_goal": [
        "i want to become a game designer", "my goal is to be a game designer",
        "i'm aiming to work in game design", "i hope to become a game designer",
        "one day i want to design games professionally",
    ],
    "weekly_habit": [
        "i go to the gym every week", "i hit the gym weekly",
        "every week i work out at the gym", "i do a weekly gym session",
        "going to the gym is my weekly routine",
    ],
    "relationship_named": [
        "my girlfriend is hailey", "i'm dating hailey", "hailey is my girlfriend",
        "my partner's name is hailey", "i have a girlfriend named hailey",
    ],
}

MT7 = [
    ("affirm", "i eat fish"),
    ("negate", "i don't eat fish"),
    ("cease", "i stopped eating fish"),
]
MT8 = [
    ("name", "my name is arjun", True),
    ("dog", "my dog's name is arjun", False),
    ("brother", "my brother's name is arjun", False),
    ("friend", "my friend's name is arjun", False),
]


def _sig(snapshot, norm):
    """Active-node signature: set of (dim, key, normalised value)."""
    return frozenset(
        (n["dimension"], n["key"], norm(n["value"]))
        for n in snapshot.get("nodes", []) if n.get("status") == "active"
    )


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider", choices=["mock", "real"], default="real")
    ap.add_argument("--report", default="results/consistency")
    args = ap.parse_args(argv)

    os.environ["PCSG_TEST_MODE"] = "1"
    if args.provider == "mock":
        os.environ["LLM_PROVIDER"] = "mock"
    os.environ["DB_PATH"] = str(pathlib.Path(tempfile.gettempdir()) / f"pcsg_mt_{uuid.uuid4().hex[:6]}.db")

    from app.config import settings
    if args.provider == "mock":
        settings.llm.provider = "mock"
    else:
        settings.llm.max_tokens = min(settings.llm.max_tokens, 512)  # TEST-ONLY free-tier cap
    settings.db_path = pathlib.Path(os.environ["DB_PATH"])
    from app import db, ontology
    db.init_db()
    from app.main import app
    from tests.robustness.runner import Client
    norm = ontology.normalize_value

    client = Client(app)

    def run_fresh(messages):
        client.reset()
        client.register()
        sid = None
        for m in messages:
            r = client.chat(m, sid)
            sid = r["session_id"]
        return client.snapshot()

    out = {"MT1": {}, "MT6": {}, "MT7": {}, "MT8": {}}

    # ── MT-1 ──
    for fam, phrasings in MT1_FAMILIES.items():
        sigs = [_sig(run_fresh([p]), norm) for p in phrasings]
        pairs = list(itertools.combinations(range(len(sigs)), 2))
        agree = sum(1 for i, j in pairs if sigs[i] == sigs[j])
        score = agree / len(pairs)
        out["MT1"][fam] = {"consistency": round(score, 3),
                           "signatures": [sorted(list(s)) for s in sigs]}
        print(f"MT1 {fam:22} consistency={score:.2f}")

    # ── MT-6 idempotency ──
    snap = run_fresh(["i love chess"] * 5)
    chess = [n for n in snap["nodes"] if norm(n["value"]) == "chess" and n["status"] == "active"]
    superseded = [n for n in snap["nodes"] if norm(n["value"]) == "chess" and n["status"] == "superseded"]
    ok6 = len(chess) == 1 and not superseded and chess and chess[0]["confidence_calibrated"] < 1.0
    out["MT6"] = {"ok": bool(ok6), "active_chess_nodes": len(chess), "superseded": len(superseded),
                  "conf": chess[0]["confidence_calibrated"] if chess else None}
    print(f"MT6 idempotency ok={ok6} (nodes={len(chess)}, superseded={len(superseded)})")

    # ── MT-7 negation discrimination ──
    sigs7 = {label: _sig(run_fresh([msg]), norm) for label, msg in MT7}
    distinct = len({frozenset(s) for s in sigs7.values()})
    out["MT7"] = {"distinct_outcomes": distinct,
                  "by_label": {k: sorted(list(v)) for k, v in sigs7.items()}}
    print(f"MT7 negation: {distinct}/3 distinct outcomes")

    # ── MT-8 subject discrimination ──
    res8 = {}
    for label, msg, expect_identity in MT8:
        snap = run_fresh([msg])
        has_identity_name = any(n["dimension"] == "identity" and n["key"] == "name"
                                and n["status"] == "active" for n in snap["nodes"])
        res8[label] = {"touched_identity": has_identity_name, "expected": expect_identity,
                       "ok": has_identity_name == expect_identity}
        print(f"MT8 {label:8} touched_identity={has_identity_name} expected={expect_identity}")
    out["MT8"] = res8

    run_id = time.strftime("%Y%m%d_%H%M%S")
    outdir = pathlib.Path(args.report) / run_id
    outdir.mkdir(parents=True, exist_ok=True)
    (outdir / "metamorphic.json").write_text(json.dumps(out, indent=2), encoding="utf-8")

    mt1_scores = [v["consistency"] for v in out["MT1"].values()]
    print(f"\nMT1 mean consistency = {sum(mt1_scores)/len(mt1_scores):.3f} "
          f"(families >=0.90: {sum(1 for s in mt1_scores if s >= 0.9)}/{len(mt1_scores)})")
    print(f"MT7 distinct = {out['MT7']['distinct_outcomes']}/3   "
          f"MT8 ok = {sum(1 for r in res8.values() if r['ok'])}/4   MT6 ok = {out['MT6']['ok']}")
    print(f"report: {outdir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
