"""STEP 2 overnight paraphrase robustness (2026-10-25; Nemotron 3 Super withdrawn).

Paraphraser = minimaxai/minimax-m3.1-flash (temperature 0.9), ONE style per persona
cycling casual / formal / terse / typo-prone. Each truth value must survive: a
deterministic check that the normalised value (or a listed synonym) still appears;
up to 2 regenerations, else keep the ORIGINAL message and log it. The paraphraser
NEVER sees PCSG's extraction prompts. Replay the paraphrased turns through the FULL
pipeline with gpt-oss-20b (self-consistency k=3), label A/C/E, and compare with
STEP 4's ORIGINAL-message gpt-oss results on the SAME 12 personas (persona-paired).

Resumable by persona; respects results/PAUSE and --until. Logs llm_cache.jsonl.

  python -m pcsg.paraphrase_overnight --out results/paraphrase/<run> --model2-run model2_<id> [--until HH:MM] [--workers 4]
"""
from __future__ import annotations
import argparse, datetime as dt, json, pathlib, sys, time, threading
import concurrent.futures as cf
import numpy as np

STYLES = ["casual", "formal", "terse", "typo-prone"]
STYLE_INSTR = {
    "casual": "Rewrite this chat message in a casual, conversational tone. Keep ALL facts and specific values.",
    "formal": "Rewrite this chat message in a formal, polished tone. Keep ALL facts and specific values.",
    "terse": "Rewrite this chat message as tersely as possible (drop filler) but keep ALL facts and specific values.",
    "typo-prone": "Rewrite this chat message casually with a couple of realistic typos, but keep ALL facts and specific values readable.",
}
LABEL = "model: gpt-oss-20b (Nemotron 3 Super withdrawn from the hosted API on 2026-10-25); paraphraser: minimax-m3.1-flash"


def _setup():
    import os
    os.environ["PCSG_SELF_CONSISTENCY"] = "1"  # STEP 2 requires k=3 self-consistency
    from app.config import settings
    for role in ("chat", "classifier", "extractor", "simulator"):
        settings.llm.models[role] = "openai/gpt-oss-20b"
    settings.llm.request_params = {**settings.llm.request_params,
                                   "openai/gpt-oss-20b": {"reasoning_effort": "low"}}
    settings.pipeline.salience_threshold = 0.0
    return settings


def _paraphrase(text, style):
    from app import llm
    msgs = [{"role": "system", "content": STYLE_INSTR[style] + " Output ONLY the rewritten message."},
            {"role": "user", "content": text}]
    r = llm.complete(msgs, model="minimaxai/minimax-m3.1-flash", temperature=0.9, max_tokens=256)
    return (r.get("text") or "").strip() or text


def _truth_values(persona):
    from pcsg.analysis import labelling
    vals = set()
    for f in persona.facts.values():
        if f.kind != "end":
            vals.add(labelling._norm(f.value))
    return {v for v in vals if v}


def _preserved(original, paraphrase, truth_vals):
    """Which normalised truth values were present in the ORIGINAL and must survive."""
    from pcsg.analysis import labelling
    o = labelling._norm(original); p = labelling._norm(paraphrase)
    need = {v for v in truth_vals if v and v in o}
    missing = {v for v in need if v not in p}
    return need, missing


def _paraphrase_turn(text, style, truth_vals):
    """Paraphrase with up to 2 regenerations if a truth value is dropped; else original."""
    best = None
    for _ in range(3):
        cand = _paraphrase(text, style)
        need, missing = _preserved(text, cand, truth_vals)
        if not missing:
            return cand, False  # preserved
        best = cand
    return text, True  # kept original (could not preserve); logged


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--model2-run", required=True, help="STEP 4 run id (original-message gpt-oss) to pair against")
    ap.add_argument("--until", default=None)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--rate", type=float, default=35)
    args = ap.parse_args(argv)
    REPO = pathlib.Path(__file__).resolve().parents[2]
    outdir = pathlib.Path(args.out); outdir.mkdir(parents=True, exist_ok=True)
    _setup()
    from app import llm, db, pipeline
    from app.db import get_conn, now_iso, set_db_path
    from app.utils import new_id
    from pcsg import datagen
    from pcsg.analysis import labelling
    llm.set_rate_limit(args.rate); llm.set_cache_log(outdir / "llm_cache.jsonl")

    sel = json.loads((REPO / "results" / "overnight_personas.json").read_text())["selected_12"]
    personas = {p.pid: p for p in datagen.generate_personas(2026, 60) if p.pid in sel}
    truth = {pid: [{"id": k, "dimension": f.dimension, "value": f.value}
                   for k, f in p.facts.items() if f.kind != "end" and f.superseded_by is None]
             for pid, p in personas.items()}
    dbfile = pathlib.Path(__import__("tempfile").gettempdir()) / f"pcsg_paraphrase_{outdir.name}.db"
    set_db_path(dbfile); db.init_db()

    ck = outdir / "checkpoint.json"
    done = set(json.loads(ck.read_text()).get("done", [])) if ck.exists() else set()
    stats_path = outdir / "paraphrase_turns.jsonl"
    lock = threading.Lock()
    pause = REPO / "results" / "PAUSE"
    cutoff = None
    if args.until:
        hh, mm = map(int, args.until.split(":")); now = dt.datetime.now()
        cutoff = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if cutoff < now: cutoff += dt.timedelta(days=1)
    stored = {pid: [] for pid in sel}  # labelled records per persona (filled after replay from DB)
    turnlog = []

    def _do(pid):
        if pause.exists() or (cutoff and dt.datetime.now() >= cutoff):
            return "skip"
        p = personas[pid]; style = STYLES[sel.index(pid) % len(STYLES)]
        tv = _truth_values(p)
        try:
            uid, sid = new_id("user"), new_id("sess")
            with get_conn() as c:
                c.execute("INSERT INTO users(id,label,created_at)VALUES(?,?,?)", (uid, p.name, now_iso()))
                c.execute("INSERT INTO sessions(id,user_id,title,created_at)VALUES(?,?,?,?)", (sid, uid, "para", now_iso()))
            rows = []
            for t in p.turns:
                para, kept = _paraphrase_turn(t.text, style, tv)
                pipeline.process_turn(para, sid, uid)
                rows.append({"persona": pid, "style": style, "orig": t.text, "para": para,
                             "kept_original": kept, "orig_len": len(t.text.split()), "para_len": len(para.split())})
            # collect this persona's stored signals + label A/C/E
            with get_conn() as c:
                sigs = c.execute("SELECT cs.dimension,cs.value,cs.verbalized,cs.self_consistency "
                                 "FROM confidence_signals cs JOIN nodes n ON n.id=cs.node_id "
                                 "WHERE n.user_id=?", (uid,)).fetchall()
            recs = []
            for s in sigs:
                labs = labelling.labels_all({"dimension": s["dimension"], "value": s["value"]}, truth[pid])
                recs.append({"persona": pid, "correct_A": labs["A"], "correct_C": labs["C"], "correct_E": labs["E"],
                             "verbalized": s["verbalized"], "self_consistency": s["self_consistency"]})
            with lock:
                stored[pid] = recs; turnlog.extend(rows)
                done.add(pid)
                with open(stats_path, "a", encoding="utf-8") as fh:
                    for r in rows: fh.write(json.dumps(r) + "\n")
                ck.write_text(json.dumps({"done": sorted(done)}), encoding="utf-8")
                with open(outdir / "labelled_paraphrase.jsonl", "a", encoding="utf-8") as fh:
                    for r in recs: fh.write(json.dumps(r) + "\n")
            return "done"
        except Exception as e:  # noqa: BLE001
            print(f"[para] persona {pid} failed: {type(e).__name__}: {str(e)[:80]}")
            return "fail"

    todo = [pid for pid in sel if pid not in done]
    print(f"[para] {len(todo)} personas todo (workers={args.workers}) cutoff={args.until or 'none'}")
    t0 = time.time()
    with cf.ThreadPoolExecutor(max_workers=args.workers) as ex:
        for f in cf.as_completed([ex.submit(_do, pid) for pid in todo]):
            f.result()
    _write_report(outdir, args.model2_run, REPO)
    print(f"[para] done in {(time.time()-t0)/60:.1f} min -> {outdir}")
    return 0


def _per_signal(recs, sig):
    from pcsg.analysis import calibration as C
    rows = [(float(r[sig]), int(r["correct_A"]), r["persona"]) for r in recs if r.get(sig) is not None]
    if not rows: return None
    p = np.array([a for a, _, _ in rows]); y = np.array([b for _, b, _ in rows]); g = np.array([c for _, _, c in rows])
    return {"n": len(rows), "acc": round(float(y.mean()), 4),
            "ece": round(C.ece(p, y, 10), 4), "brier": round(C.brier(p, y), 4),
            "auroc": round(C.auroc(p, y), 4),
            "auroc_ci": [round(x, 4) for x in C.bootstrap_ci(C.auroc, p, y, g, n=1000, seed=2026)]}


def _write_report(outdir, model2_run, REPO):
    lp = outdir / "labelled_paraphrase.jsonl"
    para = [json.loads(l) for l in lp.read_text(encoding="utf-8").splitlines()] if lp.exists() else []
    op = REPO / "results" / "runs" / model2_run / "labelled.jsonl"
    sel = json.loads((REPO / "results" / "overnight_personas.json").read_text())["selected_12"]
    orig = [r for r in ([json.loads(l) for l in op.read_text(encoding="utf-8").splitlines()] if op.exists() else [])
            if r.get("persona") in sel]
    tl = outdir / "paraphrase_turns.jsonl"
    turns = [json.loads(l) for l in tl.read_text(encoding="utf-8").splitlines()] if tl.exists() else []
    uniq = len({t["para"] for t in turns}); kept = sum(1 for t in turns if t.get("kept_original"))
    ol = [t["orig_len"] for t in turns] or [0]; pl = [t["para_len"] for t in turns] or [0]
    L = ["# PARAPHRASE — robustness (STEP 2)", "", f"**{LABEL}**", "",
         f"Same 12 personas (seed 2026): {sel}", "",
         f"Messages paraphrased: {len(turns)} | unique-paraphrase ratio: {round(uniq/max(len(turns),1),3)} | "
         f"kept-original (value could not be preserved in 2 retries): {kept}", "",
         f"Mean message length (words): original {sum(ol)/len(ol):.1f} -> paraphrased {sum(pl)/len(pl):.1f}", "",
         "## Per-signal (rule A) — paraphrased vs STEP-4 original (same 12 personas, persona-paired AUROC CI)", "",
         "| condition | signal | n | acc | ECE | Brier | AUROC [95% CI] |", "|---|---|---|---|---|---|---|"]
    for tag, recs in (("paraphrased", para), ("original(STEP4)", orig)):
        for sig in ("verbalized", "self_consistency"):
            m = _per_signal(recs, sig)
            L.append(f"| {tag} | {sig} | {m['n']} | {m['acc']} | {m['ece']} | {m['brier']} | {m['auroc']} {m['auroc_ci']} |"
                     if m else f"| {tag} | {sig} | 0 | — | — | — | — |")
    L += ["", "Comparison is paired on the SAME 12 personas and SAME model (gpt-oss-20b); it isolates the "
          "effect of paraphrasing the user messages on extraction accuracy and signal calibration.", ""]
    (outdir / "PARAPHRASE.md").write_text("\n".join(L), encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())
