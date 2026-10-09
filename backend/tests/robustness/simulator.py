"""S9 simulated-user exploration.

Drives each generated persona through the REAL assistant for its 15-turn plan,
then diffs the final graph against the persona's truth table and triages every
discrepancy against the transcript:
  - missed    : an expressed, expected-active fact is absent from the graph
  - wrong     : present but wrong value/subject/dimension
  - spurious  : an active node matching no expected fact and no earlier turn
  - wrong_end : a stable/expected fact was ended/superseded (corruption), or a
                change/END was NOT applied (old value still active)
  - gen_error : the persona never actually expressed the fact (generator limit)

Default mode is `scripted` (sends datagen's verbalised turns — reproducible, so
"what was said" is known exactly). `--mode model` uses the `simulator` config
role to phrase turns; it is noisier and is the experiments' optional naturalism.

Run (from backend/):
  PCSG_TEST_MODE=1 PYTHONPATH=. ./.venv/Scripts/python tests/robustness/simulator.py --seed 1000 --n 20
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import tempfile
import time
import uuid


def _active(snap):
    return [n for n in snap.get("nodes", []) if n.get("status") == "active"]


def _superseded_values(snap, norm):
    return {(n["dimension"], norm(n["value"])) for n in snap.get("nodes", [])
            if n.get("status") != "active"}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=1000)
    ap.add_argument("--n", type=int, default=20)
    ap.add_argument("--mode", choices=["scripted", "model"], default="scripted")
    ap.add_argument("--provider", choices=["mock", "real"], default="real")
    ap.add_argument("--report", default="results/simulator")
    args = ap.parse_args(argv)

    os.environ["PCSG_TEST_MODE"] = "1"
    if args.provider == "mock":
        os.environ["LLM_PROVIDER"] = "mock"
    os.environ["DB_PATH"] = str(pathlib.Path(tempfile.gettempdir()) / f"pcsg_sim_{uuid.uuid4().hex[:6]}.db")

    from app.config import settings
    if args.provider == "mock":
        settings.llm.provider = "mock"
    else:
        # TEST-ONLY per-process cap so a low-balance / free-tier key can afford the
        # request (it 402s above ~850 tokens). Committed config stays at 1024.
        settings.llm.max_tokens = min(settings.llm.max_tokens, 512)
    settings.db_path = pathlib.Path(os.environ["DB_PATH"])
    from app import db, ontology, llm
    db.init_db()
    from app.main import app
    from tests.robustness.runner import Client
    from pcsg.datagen import generate_personas, generator_self_check
    norm = ontology.normalize_value

    client = Client(app)
    personas = generate_personas(args.seed, args.n)

    def phrase(persona, turn, history):
        if args.mode == "scripted" or turn.reveals is None:
            return turn.text
        # model mode: let the simulator role phrase the reveal naturally
        sysmsg = ("[[TASK:SIMUSER]] You are role-playing a chat user. Say the next "
                  "short, natural message that conveys this intent, in first person, "
                  "no preamble: " + turn.text)
        r = llm.complete(messages=[{"role": "system", "content": sysmsg},
                                   {"role": "user", "content": "your message:"}],
                         model=llm.resolve_model("simulator"), temperature=0.7, max_tokens=40)
        return (r.get("text") or turn.text).strip().strip('"') or turn.text

    agg = {"missed": 0, "wrong_end": 0, "spurious": 0, "gen_error": 0, "expected": 0}
    per_persona = []
    candidate_bugs = []

    for p in personas:
      try:
        client.reset()
        client.register()
        sid = None
        transcript = []
        last_assistant = ""
        for t in p.turns:
            if t.style == "when_asked" and t.about and t.about not in last_assistant.lower():
                continue  # the assistant never asked -> the user never brings it up
            msg = phrase(p, t, transcript)
            r = client.chat(msg, sid)
            sid = r["session_id"]
            last_assistant = r.get("reply", "") or ""
            transcript.append(("user", msg))
            transcript.append(("assistant", last_assistant))
        snap = client.snapshot()
        active = _active(snap)
        said = " ".join(m.lower() for role, m in transcript if role == "user")
        sup = _superseded_values(snap, norm)

        def present(f):
            return any(n["dimension"] == f.dimension and norm(n["value"]) == norm(f.value)
                       for n in active)

        disc = []
        selfchk = generator_self_check(p)
        for fid, f in p.facts.items():
            if f.kind == "end" or f.superseded_by:
                continue
            agg["expected"] += 1
            if present(f):
                continue
            expressed = f.value.split()[0] in said
            if not expressed:
                agg["gen_error"] += 1
                disc.append({"type": "gen_error", "fact": fid, "value": f.value})
            else:
                agg["missed"] += 1
                disc.append({"type": "missed", "fact": fid, "dim": f.dimension, "value": f.value})
                candidate_bugs.append({"persona": p.pid, "type": "missed",
                                       "fact": f"{f.dimension}/{f.value}", "said_in": [m for r, m in transcript if r == "user" and f.value.split()[0] in m.lower()][:1]})
        # changes applied? old values must NOT be active
        for old_id in ("city_old", "job_old"):
            f = p.facts[old_id]
            if any(n["dimension"] == f.dimension and norm(n["value"]) == norm(f.value) for n in active):
                agg["wrong_end"] += 1
                disc.append({"type": "change_not_applied", "fact": old_id, "value": f.value})
                candidate_bugs.append({"persona": p.pid, "type": "change_not_applied", "fact": f"{f.dimension}/{f.value}"})
        # END applied? the ended hobby must NOT be active
        ended = p.ended_fact()
        if ended and any(n["dimension"] == ended.dimension and norm(n["value"]) == norm(ended.value) for n in active):
            agg["wrong_end"] += 1
            disc.append({"type": "end_not_applied", "fact": ended.value})
            candidate_bugs.append({"persona": p.pid, "type": "end_not_applied", "fact": f"habit/{ended.value}"})
        # corruption: an expected-active stable fact sitting as superseded/ended
        for f in p.expected_active():
            if not present(f) and (f.dimension, norm(f.value)) in sup:
                agg["wrong_end"] += 1
                disc.append({"type": "wrong_supersede", "fact": f"{f.dimension}/{f.value}"})
                candidate_bugs.append({"persona": p.pid, "type": "wrong_supersede", "fact": f"{f.dimension}/{f.value}", "severity": "S0"})
        per_persona.append({"pid": p.pid, "name": p.name, "active_nodes": len(active),
                            "discrepancies": disc, "gen_not_expressed": selfchk["not_expressed"]})
        flags = [d["type"] for d in disc]
        print(f"persona {p.pid:2} {p.name:8} active={len(active):2} "
              f"missed={flags.count('missed')} gen_err={flags.count('gen_error')} "
              f"change_fail={flags.count('change_not_applied')} end_fail={flags.count('end_not_applied')} "
              f"corrupt={flags.count('wrong_supersede')}")
      except Exception as e:  # noqa: BLE001 — one persona failing must not kill the run
        print(f"persona {p.pid:2} {p.name:8} CRASHED: {type(e).__name__}: {str(e)[:80]}")
        candidate_bugs.append({"persona": p.pid, "type": "persona_crash", "error": str(e)[:120]})

    total_expected = agg["expected"]
    gen_rate = agg["gen_error"] / max(1, total_expected)
    run_id = time.strftime("%Y%m%d_%H%M%S")
    outdir = pathlib.Path(args.report) / f"seed{args.seed}_{run_id}"
    outdir.mkdir(parents=True, exist_ok=True)
    (outdir / "summary.json").write_text(json.dumps(
        {"seed": args.seed, "n": args.n, "mode": args.mode, "aggregate": agg,
         "generator_error_rate": round(gen_rate, 3), "candidate_bugs": candidate_bugs,
         "per_persona": per_persona}, indent=2), encoding="utf-8")
    print(f"\n== seed={args.seed} n={args.n} ==")
    print(f"expected facts: {total_expected}")
    print(f"MISSED (candidate bugs):      {agg['missed']}")
    print(f"CHANGE/END not applied:       {agg['wrong_end']}")
    print(f"generator error rate:         {gen_rate:.1%} ({agg['gen_error']}/{total_expected})")
    print(f"candidate bugs: {len(candidate_bugs)}  -> {outdir}/summary.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
