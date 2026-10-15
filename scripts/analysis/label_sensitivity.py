"""Offline, read-only label-sensitivity analysis for a completed Tier-1 run.
No API calls, no pipeline changes, does not touch the running queue. Reconstructs
test assertions from the run DB (read-only) + truth.json + datagen splits, relabels
them under 4 deterministic definitions, and writes:
  results/analysis/label_audit.csv        (+ _key.csv with hidden labels)
  results/analysis/LABEL_SENSITIVITY.md

Usage: python scripts/analysis/label_sensitivity.py <run_id>
"""
from __future__ import annotations
import csv, json, os, random, sqlite3, tempfile, sys, pathlib, collections, math

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "backend"))
import numpy as np
from app import ontology
from pcsg.analysis import calibration as C
from pcsg import datagen

RUN = sys.argv[1] if len(sys.argv) > 1 else "main_20261015_000553"
RUND = REPO / "results" / "runs" / RUN
OUT = REPO / "results" / "analysis"; OUT.mkdir(parents=True, exist_ok=True)
DB = os.path.join(tempfile.gettempdir(), f"pcsg_exp_{RUN}.db")
cfg = json.loads((RUND / "config.json").read_text())
SEED, N = int(cfg.get("seed", 2026)), int(cfg.get("n_personas", 60))
truth = json.loads((RUND / "truth.json").read_text())   # {pid(str): [{id,dimension,value}]}

# CORRECT per-persona mapping. datagen names collide (28 unique for 60 personas),
# so mapping an assertion to a persona by NAME is ambiguous and is exactly the bug
# that corrupts the run's own calibration split. Replay creates one DB user per
# persona in pid order, so user[i]-by-created_at == personas[i] (validated:
# user.label==personas[i].name for all 60). We map via that order -> correct pid.
personas = datagen.generate_personas(SEED, N)
pid2split = {p.pid: p.split for p in personas}

OPMAP = {"created": "ADD", "reinforced": "ADD", "superseded": "UPDATE", "ended": "END",
         "refined": "REFINE", "linked": "ADD"}

c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True); c.row_factory = sqlite3.Row
_users = c.execute("SELECT id, label, created_at FROM users ORDER BY created_at").fetchall()
uid2pid = {u["id"]: personas[i].pid for i, u in enumerate(_users) if i < len(personas)}
# the run's BUGGY name-collapse mapping (first/lowest pid wins) -> reproduce for comparison
name2pid_firstwins = {}
for p in personas:
    name2pid_firstwins.setdefault(p.name, p.pid)

rows = c.execute("""
  SELECT cs.id, cs.node_id, cs.message_id, cs.dimension, cs.key, cs.value,
         cs.verbalized, cs.self_consistency, cs.evidence, n.user_id AS user_id, u.label AS uname
  FROM confidence_signals cs JOIN nodes n ON n.id=cs.node_id JOIN users u ON u.id=n.user_id
""").fetchall()

def msg_text(mid):
    r = c.execute("SELECT content FROM messages WHERE id=?", (mid,)).fetchone()
    return r["content"] if r else ""
def quote_text(nid, mid):
    r = c.execute("SELECT quote FROM evidence WHERE node_id=? AND message_id=? LIMIT 1", (nid, mid)).fetchone()
    return r["quote"] if r else ""
def op_of(nid, mid):
    r = c.execute("SELECT action FROM events WHERE node_id=? AND message_id=? ORDER BY created_at LIMIT 1", (nid, mid)).fetchone()
    return OPMAP.get(r["action"], (r["action"] or "ADD").upper()) if r else "ADD"

A = []  # reconstructed assertions (CORRECT per-user mapping)
run_split_counts = collections.Counter()   # the run's BUGGY name-collapse split, for comparison
for r in rows:
    pid = uid2pid.get(r["user_id"])
    if pid is None:
        continue
    # what the run's _collect_signals did: map by name -> first/lowest pid
    rpid = name2pid_firstwins.get(r["uname"])
    run_split_counts[pid2split.get(rpid, "?")] += 1
    A.append({
        "id": r["id"], "pid": pid, "split": pid2split.get(pid, "fit"),
        "dimension": r["dimension"], "key": r["key"], "value": r["value"],
        "verbalized": r["verbalized"], "self_consistency": r["self_consistency"],
        "evidence": r["evidence"], "node_id": r["node_id"], "message_id": r["message_id"],
    })

# ── normalisation + label definitions ────────────────────────────────────── #
_SYN = {"nyc": "new york", "sf": "san francisco", "veg": "vegetarian"}   # from labelling.py
def norm(v):
    x = ontology.normalize_value(str(v or "")); return _SYN.get(x, x)
KEY_SYNONYMS = {  # explicit, listed in the report (bidirectional)
    "diet": "food_preference", "food_preference": "diet",
    "food_likes": "likes", "likes": "food_likes",
    "occupation": "job", "job": "occupation",
    "location": "city", "city": "location",
    "music_genre": "music", "music": "music_genre",
}
def toks(v): return set(norm(v).split())
def fuzzy(a, b):
    na, nb = norm(a), norm(b)
    if not na or not nb: return False
    if na in nb or nb in na: return True
    ta, tb = toks(a), toks(b)
    if not ta or not tb: return False
    inter = len(ta & tb); return inter / max(len(ta), len(tb)) >= 0.5

def labelA(a, tf): return 1 if any(f["dimension"] == a["dimension"] and norm(f["value"]) == norm(a["value"]) for f in tf) else 0
def labelB(a, tf): return 1 if any(norm(f["value"]) == norm(a["value"]) for f in tf) else 0
def labelC(a, tf): return 1 if (labelB(a, tf) or any(fuzzy(f["value"], a["value"]) for f in tf)) else 0
def labelD(a, tf):
    if labelC(a, tf): return 1
    k = a.get("key")
    for f in tf:
        fid = f.get("id")
        if k and fid and KEY_SYNONYMS.get(k) == fid and fuzzy(f["value"], a["value"]):
            return 1
    return 0

def tfacts(pid): return truth.get(str(pid), truth.get(pid, []))
for a in A:
    tf = tfacts(a["pid"])
    a["A"] = labelA(a, tf); a["B"] = labelB(a, tf); a["C"] = labelC(a, tf); a["D"] = labelD(a, tf)

test = [a for a in A if a["split"] == "test"]

# ── Q1 split counts ───────────────────────────────────────────────────────── #
persona_split = collections.Counter(pid2split[p] for p in pid2split)
assert_split = collections.Counter(a["split"] for a in A)

# ── metrics ──────────────────────────────────────────────────────────────── #
def metrics(subset_test, subset_fit, labelkey, sigkey):
    tp = np.array([a[sigkey] for a in subset_test if a.get(sigkey) is not None], float)
    ty = np.array([a[labelkey] for a in subset_test if a.get(sigkey) is not None], float)
    fp = np.array([a[sigkey] for a in subset_fit if a.get(sigkey) is not None], float)
    fy = np.array([a[labelkey] for a in subset_fit if a.get(sigkey) is not None], float)
    acc = ty.mean() if len(ty) else float("nan")
    out = {"n": len(ty), "acc": acc, "base_brier": acc * (1 - acc) if len(ty) else float("nan")}
    def block(q):
        return {"ece": round(C.ece(q, ty, 10, "uniform"), 4), "brier": round(C.brier(q, ty), 4),
                "auroc": round(C.auroc(q, ty), 4)}
    out["raw"] = block(np.clip(tp, 1e-6, 1 - 1e-6))
    try:
        ap = C.fit_platt(fp, fy) if len(fp) >= 5 and len(set(fy.tolist())) > 1 else (lambda x: x)
        out["platt"] = block(np.asarray(ap(tp), float))
    except Exception as e:
        out["platt"] = {"error": str(e)[:50]}
    return out

fit = [a for a in A if a["split"] == "fit"]
defs = {}
for d in ("A", "B", "C", "D"):
    defs[d] = {"acc": np.mean([a[d] for a in test]),
               "verbalized": metrics(test, fit, d, "verbalized"),
               "self_consistency": metrics(test, fit, d, "self_consistency")}

# flips A vs {B,C,D}
flips = {}
for d in ("B", "C", "D"):
    changed = [a for a in test if a[d] != a["A"]]
    flips[d] = changed

# stratify A and C by operation + dimension
def op_of_cached(a):
    if "op" not in a: a["op"] = op_of(a["node_id"], a["message_id"])
    return a["op"]
def strat(labelkey, groupfn):
    g = collections.defaultdict(list)
    for a in test: g[groupfn(a)].append(a)
    res = {}
    for k, items in sorted(g.items(), key=lambda kv: str(kv[0])):
        y = np.array([a[labelkey] for a in items], float)
        v = np.array([a["verbalized"] for a in items], float)
        res[k] = {"n": len(items), "acc": round(y.mean(), 4),
                  "auroc": round(C.auroc(np.clip(v, 1e-6, 1 - 1e-6), y), 4) if len(set(y.tolist())) > 1 else "n/a(1class)"}
    return res

strat_tbl = {}
for lab in ("A", "C"):
    strat_tbl[lab] = {"operation": strat(lab, op_of_cached), "dimension": strat(lab, lambda a: a["dimension"])}

# ── audit CSV: 119 label-0 + 30 random label-1, shuffled; key file separate ── #
rng = random.Random(2026)
zeros = [a for a in test if a["A"] == 0]
ones = [a for a in test if a["A"] == 1]
sample = zeros + rng.sample(ones, min(30, len(ones)))
rng.shuffle(sample)
with open(OUT / "label_audit.csv", "w", newline="", encoding="utf-8") as fh:
    w = csv.writer(fh)
    w.writerow(["id", "persona_id", "source_message", "evidence_quote", "subject", "dimension", "key", "value", "operation", "truth_facts", "human_label"])
    for a in sample:
        tf = tfacts(a["pid"])
        tfs = "; ".join(f"{f['dimension']}={f['value']}" for f in tf)
        subj = "entity" if a["dimension"] == "relationship" and a["key"] not in ("", None) else "self"
        w.writerow([a["id"], a["pid"], msg_text(a["message_id"]), quote_text(a["node_id"], a["message_id"]),
                    subj, a["dimension"], a["key"], a["value"], op_of_cached(a), tfs, ""])
with open(OUT / "label_audit_key.csv", "w", newline="", encoding="utf-8") as fh:
    w = csv.writer(fh); w.writerow(["id", "label_A", "A", "B", "C", "D"])
    for a in sample: w.writerow([a["id"], a["A"], a["A"], a["B"], a["C"], a["D"]])

# ── print a compact digest the .md is built from ────────────────────────────
def fmt(m):
    return (f"n={m['n']} acc={m['acc']:.4f} base_brier={m['base_brier']:.4f} | "
            f"raw ece={m['raw']['ece']} brier={m['raw']['brier']} auroc={m['raw']['auroc']} | "
            f"platt ece={m['platt'].get('ece')} brier={m['platt'].get('brier')} auroc={m['platt'].get('auroc')}")
print("PERSONA_SPLIT", dict(persona_split))
print("ASSERT_SPLIT (corrected per-user)", dict(assert_split))
print("ASSERT_SPLIT (run's name-collapse, matches labelled.jsonl)", dict(run_split_counts))
for d in ("A", "B", "C", "D"):
    print(f"DEF {d} test_acc={defs[d]['acc']:.4f}")
    print(f"  verbalized: {fmt(defs[d]['verbalized'])}")
    print(f"  self_cons : {fmt(defs[d]['self_consistency'])}")
for d in ("B", "C", "D"):
    print(f"FLIPS A->{d}: {len(flips[d])}")
for lab in ("A", "C"):
    print(f"STRAT {lab} by operation:", strat_tbl[lab]["operation"])
    print(f"STRAT {lab} by dimension:", strat_tbl[lab]["dimension"])
print("AUDIT rows:", len(sample), "zeros:", len(zeros), "ones_sampled:", min(30, len(ones)))

# stash everything for the .md writer (write a json the companion step reads)
digest = {
    "run": RUN, "persona_split": dict(persona_split),
    "assert_split_corrected": dict(assert_split), "assert_split_run_namecollapse": dict(run_split_counts),
    "name_collision": {"personas": len(personas), "unique_names": len({p.name for p in personas}),
                       "distinct_personas_in_run_labels": len(set(r['uname'] for r in rows))},
    "defs": {d: {"acc": float(defs[d]["acc"]), "verbalized": defs[d]["verbalized"], "self_consistency": defs[d]["self_consistency"]} for d in defs},
    "flips": {d: {"count": len(flips[d]),
                  "examples": [{"value": a["value"], "dimension": a["dimension"], "key": a["key"],
                                "A": a["A"], d: a[d], "truth": "; ".join(f"{f['dimension']}={f['value']}" for f in tfacts(a["pid"]))}
                               for a in flips[d][:10]]} for d in ("B", "C", "D")},
    "strat": strat_tbl, "key_synonyms": KEY_SYNONYMS,
    "audit": {"rows": len(sample), "zeros": len(zeros), "ones_sampled": min(30, len(ones))},
}
json.dump(digest, open(OUT / "_digest.json", "w"), default=str, indent=2)
print("wrote", OUT / "_digest.json")
