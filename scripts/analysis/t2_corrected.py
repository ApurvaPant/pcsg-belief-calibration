"""Corrected Tier-2 calibration (offline, read-only). Per-USER persona attribution
(fixes the name-collision bug) + label E. No API calls; no pipeline files edited.
Writes under results/analysis/ only.

  python scripts/analysis/t2_corrected.py <run_id>
Outputs: T2_corrected.md, F1_corrected_{signal}.csv/.png, error_audit.csv(+_key.csv).
"""
from __future__ import annotations
import csv, json, os, random, re, sqlite3, tempfile, sys, pathlib, collections
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
truth = json.loads((RUND / "truth.json").read_text())
personas = datagen.generate_personas(SEED, N)
pid2split = {p.pid: p.split for p in personas}

c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True); c.row_factory = sqlite3.Row
users = c.execute("SELECT id,label FROM users ORDER BY created_at").fetchall()
uid2pid = {u["id"]: personas[i].pid for i, u in enumerate(users) if i < len(personas)}
OPMAP = {"created": "ADD", "reinforced": "ADD", "superseded": "UPDATE", "ended": "END", "refined": "REFINE", "linked": "ADD"}
rows = c.execute("""SELECT cs.id,cs.node_id,cs.message_id,cs.dimension,cs.key,cs.value,
  cs.verbalized,cs.self_consistency,cs.evidence,n.user_id AS uid
  FROM confidence_signals cs JOIN nodes n ON n.id=cs.node_id""").fetchall()

def op_of(nid, mid):
    r = c.execute("SELECT action FROM events WHERE node_id=? AND message_id=? ORDER BY created_at LIMIT 1", (nid, mid)).fetchone()
    return OPMAP.get(r["action"], (r["action"] or "ADD").upper()) if r else "ADD"
def msg_text(mid):
    r = c.execute("SELECT content FROM messages WHERE id=?", (mid,)).fetchone(); return r["content"] if r else ""

# ── label definitions ─────────────────────────────────────────────────────── #
_SYN = {"nyc": "new york", "sf": "san francisco", "veg": "vegetarian"}
def normA(v): x = ontology.normalize_value(str(v or "")); return _SYN.get(x, x)
_LEAD = re.compile(r"^(become an?|learn to|want to|wanna|aim to|planning to|going to|i am|i'm|i|my|to)\s+", re.I)
_LEMMA = {"coding": "code", "sketching": "sketch", "running": "run", "cooking": "cook", "painting": "paint",
          "gaming": "game", "reading": "read", "writing": "write", "swimming": "swim", "programming": "program"}
def lemma(t):
    if t in _LEMMA: return _LEMMA[t]
    if len(t) > 4 and t.endswith("ing"): return t[:-3]
    return t
def normE(v):
    s = str(v or "").lower().replace("_", " ").replace("-", " ")
    s = re.sub(r"\s+", " ", s).strip()
    prev = None
    while prev != s:
        prev = s; s = _LEAD.sub("", s)
    return " ".join(lemma(t) for t in s.split())
def toks(v): return set(normE(v).split())
_NUMRANGE = re.compile(r"^(\d+)\s*[-to]+\s*(\d+)$"); _DECADE = re.compile(r"^(\d+)s$")
def num_match(a, b):
    na, nb = normE(a), normE(b)
    da = na if na.isdigit() else None
    for x, y in ((da, nb), (nb if nb.isdigit() else None, na)):
        if x is None: continue
        n = int(x)
        m = _NUMRANGE.match(y.replace(" ", ""))
        if m and int(m.group(1)) <= n <= int(m.group(2)): return True
        m = _DECADE.match(y.replace(" ", ""))
        if m and (n // 10) * 10 == int(m.group(1)): return True
    return False
def matchE(av, tv):
    a, b = normE(av), normE(tv)
    if not a or not b: return False
    if a == b or a in b or b in a: return True
    ta, tb = toks(av), toks(tv)
    if ta and tb and len(ta & tb) / max(len(ta), len(tb)) >= 0.5: return True
    return num_match(av, tv)
def fuzzyC(av, tv):
    a, b = normA(av), normA(tv)
    if a in b or b in a: return True
    ta, tb = set(a.split()), set(b.split())
    return bool(ta and tb and len(ta & tb) / max(len(ta), len(tb)) >= 0.5)
COMPAT = {"skill", "habit", "interest"}
def tfacts(pid): return truth.get(str(pid), truth.get(pid, []))

def label(a, defn):
    tf = tfacts(a["pid"])
    if defn == "A": return 1 if any(f["dimension"] == a["dimension"] and normA(f["value"]) == normA(a["value"]) for f in tf) else 0
    if defn == "C":
        if any(normA(f["value"]) == normA(a["value"]) for f in tf): return 1
        return 1 if any(fuzzyC(f["value"], a["value"]) for f in tf) else 0
    if defn == "E_fact":  # any dimension
        return 1 if any(matchE(f["value"], a["value"]) for f in tf) else 0
    if defn == "E":       # fully_correct: same dim (skill/habit/interest compatible) + value match
        for f in tf:
            same = (f["dimension"] == a["dimension"]) or (f["dimension"] in COMPAT and a["dimension"] in COMPAT)
            if same and matchE(f["value"], a["value"]): return 1
        return 0

A = []
for r in rows:
    pid = uid2pid.get(r["uid"])
    if pid is None: continue
    a = {"id": r["id"], "pid": pid, "split": pid2split.get(pid, "fit"), "dimension": r["dimension"],
         "key": r["key"], "value": r["value"], "node_id": r["node_id"], "message_id": r["message_id"],
         "verbalized": r["verbalized"], "self_consistency": r["self_consistency"], "evidence": r["evidence"]}
    for d in ("A", "C", "E_fact", "E"): a[d] = label(a, d)
    A.append(a)
test = [a for a in A if a["split"] == "test"]; fit = [a for a in A if a["split"] == "fit"]

# ── metrics ────────────────────────────────────────────────────────────────── #
def mce_n10(probs, labels, bins=10):
    p = np.clip(probs, 1e-6, 1 - 1e-6); y = np.asarray(labels, float); edges = np.linspace(0, 1, bins + 1); worst = 0.0
    for i in range(bins):
        lo, hi = edges[i], edges[i + 1]; m = (p >= lo) & (p <= hi if i == bins - 1 else p < hi)
        if m.sum() >= 10: worst = max(worst, abs(p[m].mean() - y[m].mean()))
    return float(worst)
def reliability(probs, labels, bins=10):
    p = np.clip(probs, 1e-6, 1 - 1e-6); y = np.asarray(labels, float); edges = np.linspace(0, 1, bins + 1); out = []
    for i in range(bins):
        lo, hi = edges[i], edges[i + 1]; m = (p >= lo) & (p <= hi if i == bins - 1 else p < hi)
        if m.sum(): out.append((float(p[m].mean()), float(y[m].mean()), int(m.sum())))
    return out
def sig_metrics(defn, sig):
    tt = [a for a in test if a.get(sig) is not None]
    ty = np.array([a[defn] for a in tt], float); tp = np.clip(np.array([a[sig] for a in tt], float), 1e-6, 1 - 1e-6)
    tg = np.array([a["pid"] for a in tt])
    ft = [a for a in fit if a.get(sig) is not None]
    fy = np.array([a[defn] for a in ft], float); fp = np.clip(np.array([a[sig] for a in ft], float), 1e-6, 1 - 1e-6)
    acc = ty.mean(); base = acc * (1 - acc)
    def blk(q):
        q = np.clip(np.asarray(q, float), 1e-6, 1 - 1e-6); br = C.brier(q, ty)
        return {"ece10": round(C.ece(q, ty, 10, "uniform"), 4), "ece15q": round(C.ece(q, ty, 15, "quantile"), 4),
                "mce": round(mce_n10(q, ty), 4), "brier": round(br, 4),
                "bss": round(1 - br / base, 4) if base > 0 else None, "nll": round(C.nll(q, ty), 4),
                "auroc": round(C.auroc(q, ty), 4),
                "auroc_ci": [round(x, 4) for x in C.bootstrap_ci(lambda a, b: C.auroc(a, b), q, ty, tg, n=1000)]}
    res = {"n": len(tt), "acc": round(acc, 4), "base_brier": round(base, 4), "raw": blk(tp), "reliability_raw": reliability(tp, ty)}
    recal = {}
    for nm, fitter in (("temperature", C.fit_temperature), ("platt", C.fit_platt), ("isotonic", C.fit_isotonic)):
        try:
            ap = fitter(fp, fy) if len(fp) >= 5 and len(set(fy.tolist())) > 1 else (lambda x: x)
            recal[nm] = blk(np.asarray(ap(tp), float))
            if nm == "platt": res["reliability_platt"] = reliability(np.asarray(ap(tp), float), ty)
        except Exception as e: recal[nm] = {"error": str(e)[:60]}
    res["recal"] = recal
    return res

DEFS = {"A": "A", "C": "C", "E": "E"}
report = {}
for dn in DEFS:
    wrong = sum(1 for a in test if a[dn] == 0)
    report[dn] = {"acc": round(np.mean([a[dn] for a in test]), 4), "wrong": wrong,
                  "verbalized": sig_metrics(dn, "verbalized"), "self_consistency": sig_metrics(dn, "self_consistency")}
e_fact_acc = round(np.mean([a["E_fact"] for a in test]), 4)

# accuracy by dimension/operation under E
for a in test: a["op"] = op_of(a["node_id"], a["message_id"])
def grp(fn):
    g = collections.defaultdict(list)
    for a in test: g[fn(a)].append(a)
    return {k: {"n": len(v), "acc": round(np.mean([x["E"] for x in v]), 4)} for k, v in sorted(g.items(), key=lambda kv: str(kv[0]))}
by_dim = grp(lambda a: a["dimension"]); by_op = grp(lambda a: a["op"])

# ── reliability CSV + PNG (raw vs Platt), per signal ───────────────────────── #
try:
    import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
    HAVE = True
except Exception: HAVE = False
for sig in ("verbalized", "self_consistency"):
    m = report["E"][sig]
    with open(OUT / f"F1_corrected_{sig}.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh); w.writerow(["stage", "conf", "acc", "n"])
        for st, key in (("raw", "reliability_raw"), ("platt", "reliability_platt")):
            for conf, ac, n in m.get(key, []): w.writerow([st, round(conf, 4), round(ac, 4), n])
    if HAVE:
        plt.figure(figsize=(4, 4)); plt.plot([0, 1], [0, 1], "k--", lw=0.8, label="perfect")
        for st, key, mk in (("raw", "reliability_raw", "o"), ("platt", "reliability_platt", "s")):
            pts = m.get(key, [])
            if pts: plt.plot([p[0] for p in pts], [p[1] for p in pts], marker=mk, label=st)
        plt.xlabel("confidence"); plt.ylabel("accuracy"); plt.title(f"F1 corrected - {sig} (E)"); plt.legend(); plt.xlim(0, 1); plt.ylim(0, 1)
        plt.savefig(OUT / f"F1_corrected_{sig}.png", dpi=110, bbox_inches="tight"); plt.close()

# ── error_audit.csv (UTF-8 BOM): all E-wrong test + 30 random correct, shuffled ─ #
def top3_truth(a):
    tf = tfacts(a["pid"]); av = normE(a["value"])
    def sim(f):
        tv = normE(f["value"]); ta, tb = set(av.split()), set(tv.split())
        ov = len(ta & tb) / max(1, max(len(ta), len(tb)))
        return ov + (0.5 if (av in tv or tv in av) else 0)
    return "; ".join(f"{f['dimension']}={f['value']}" for f in sorted(tf, key=sim, reverse=True)[:3])
rng = random.Random(2026)
wrongE = [a for a in test if a["E"] == 0]; corr = [a for a in test if a["E"] == 1]
sample = wrongE + rng.sample(corr, min(30, len(corr))); rng.shuffle(sample)
with open(OUT / "error_audit.csv", "w", newline="", encoding="utf-8-sig") as fh:   # BOM
    w = csv.writer(fh); w.writerow(["id", "persona_id", "source_message", "subject", "dimension", "key", "value", "operation", "top3_truth_facts", "human_label"])
    for a in sample:
        subj = "entity" if a["dimension"] == "relationship" else "self"
        w.writerow([a["id"], a["pid"], msg_text(a["message_id"]), subj, a["dimension"], a["key"], a["value"], a["op"], top3_truth(a), ""])
with open(OUT / "error_audit_key.csv", "w", newline="", encoding="utf-8") as fh:
    w = csv.writer(fh); w.writerow(["id", "E", "E_fact", "A", "C"]); [w.writerow([a["id"], a["E"], a["E_fact"], a["A"], a["C"]]) for a in sample]

json.dump({"report": report, "e_fact_acc": e_fact_acc, "by_dim": by_dim, "by_op": by_op,
           "audit": {"wrongE": len(wrongE), "correct_sampled": min(30, len(corr)), "rows": len(sample)}},
          open(OUT / "_t2corr.json", "w"), default=str, indent=2)
print("wrote _t2corr.json; E wrong:", len(wrongE), "E acc:", report["E"]["acc"], "E_fact acc:", e_fact_acc)
for dn in DEFS:
    r = report[dn]
    print(f"[{dn}] acc={r['acc']} wrong={r['wrong']} | verb AUROC={r['verbalized']['raw']['auroc']} CI{r['verbalized']['raw']['auroc_ci']}"
          f" | selfcons AUROC={r['self_consistency']['raw']['auroc']} CI{r['self_consistency']['raw']['auroc_ci']}")
print("matplotlib:", HAVE)
