"""error_audit_v2 (offline, read-only). All test assertions WRONG under label A +
30 random A-correct, shuffled. Per-user persona attribution (fixes the name-collision
bug). No API calls; new output files only. Self-contained (does not import the other
analysis scripts, which run on import).

  python scripts/analysis/error_audit_v2.py <run_id>
Outputs: results/analysis/error_audit_v2.csv (UTF-8 BOM) + error_audit_v2_key.csv
"""
from __future__ import annotations
import csv, json, os, random, re, sqlite3, tempfile, sys, pathlib
REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "backend"))
from app import ontology
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
rows = c.execute("""SELECT cs.id,cs.node_id,cs.message_id,cs.dimension,cs.key,cs.value,n.user_id AS uid
  FROM confidence_signals cs JOIN nodes n ON n.id=cs.node_id""").fetchall()

def msg_text(mid):
    r = c.execute("SELECT content FROM messages WHERE id=?", (mid,)).fetchone(); return r["content"] if r else ""
def op_of(nid, mid):
    r = c.execute("SELECT action FROM events WHERE node_id=? AND message_id=? ORDER BY created_at LIMIT 1", (nid, mid)).fetchone()
    return OPMAP.get(r["action"], (r["action"] or "ADD").upper()) if r else "ADD"

# ── label A / C / E (identical definitions to t2_corrected.py) ─────────────── #
_SYN = {"nyc": "new york", "sf": "san francisco", "veg": "vegetarian"}
def normA(v): x = ontology.normalize_value(str(v or "")); return _SYN.get(x, x)
_LEAD = re.compile(r"^(become an?|learn to|want to|wanna|aim to|planning to|going to|i am|i'm|i|my|to)\s+", re.I)
_LEMMA = {"coding": "code", "sketching": "sketch", "running": "run", "cooking": "cook", "painting": "paint",
          "gaming": "game", "reading": "read", "writing": "write", "swimming": "swim", "programming": "program"}
def lemma(t):
    if t in _LEMMA: return _LEMMA[t]
    return t[:-3] if len(t) > 4 and t.endswith("ing") else t
def normE(v):
    s = re.sub(r"\s+", " ", str(v or "").lower().replace("_", " ").replace("-", " ")).strip()
    prev = None
    while prev != s: prev = s; s = _LEAD.sub("", s)
    return " ".join(lemma(t) for t in s.split())
_NUMRANGE = re.compile(r"^(\d+)[-to]+(\d+)$"); _DECADE = re.compile(r"^(\d+)s$")
def num_match(a, b):
    na, nb = normE(a), normE(b)
    for x, y in ((na if na.isdigit() else None, nb), (nb if nb.isdigit() else None, na)):
        if x is None: continue
        n = int(x); yy = y.replace(" ", "")
        m = _NUMRANGE.match(yy)
        if m and int(m.group(1)) <= n <= int(m.group(2)): return True
        m = _DECADE.match(yy)
        if m and (n // 10) * 10 == int(m.group(1)): return True
    return False
def matchE(av, tv):
    a, b = normE(av), normE(tv)
    if not a or not b: return False
    if a == b or a in b or b in a: return True
    ta, tb = set(a.split()), set(b.split())
    if ta and tb and len(ta & tb) / max(len(ta), len(tb)) >= 0.5: return True
    return num_match(av, tv)
def fuzzyC(av, tv):
    a, b = normA(av), normA(tv)
    if a in b or b in a: return True
    ta, tb = set(a.split()), set(b.split())
    return bool(ta and tb and len(ta & tb) / max(len(ta), len(tb)) >= 0.5)
COMPAT = {"skill", "habit", "interest"}
def tfacts(pid): return truth.get(str(pid), truth.get(pid, []))
def labelA(a, tf): return 1 if any(f["dimension"] == a["dimension"] and normA(f["value"]) == normA(a["value"]) for f in tf) else 0
def labelC(a, tf): return 1 if (any(normA(f["value"]) == normA(a["value"]) for f in tf) or any(fuzzyC(f["value"], a["value"]) for f in tf)) else 0
def labelE(a, tf):
    for f in tf:
        same = (f["dimension"] == a["dimension"]) or (f["dimension"] in COMPAT and a["dimension"] in COMPAT)
        if same and matchE(f["value"], a["value"]): return 1
    return 0

A = []
for r in rows:
    pid = uid2pid.get(r["uid"])
    if pid is None: continue
    a = {"id": r["id"], "pid": pid, "split": pid2split.get(pid, "fit"), "dimension": r["dimension"],
         "key": r["key"], "value": r["value"], "node_id": r["node_id"], "message_id": r["message_id"]}
    tf = tfacts(pid)
    a["A"], a["C"], a["E"] = labelA(a, tf), labelC(a, tf), labelE(a, tf)
    A.append(a)
test = [a for a in A if a["split"] == "test"]

def top3(a):
    tf = tfacts(a["pid"]); av = normE(a["value"])
    def sim(f):
        tv = normE(f["value"]); ta, tb = set(av.split()), set(tv.split())
        ov = len(ta & tb) / max(1, max(len(ta), len(tb)))
        return ov + (0.5 if (av and (av in tv or tv in av)) else 0)
    return "; ".join(f"{f['dimension']}={f['value']}" for f in sorted(tf, key=sim, reverse=True)[:3])

rng = random.Random(2026)
wrongA = [a for a in test if a["A"] == 0]
corrA = [a for a in test if a["A"] == 1]
sample = wrongA + rng.sample(corrA, min(30, len(corrA)))
rng.shuffle(sample)

with open(OUT / "error_audit_v2.csv", "w", newline="", encoding="utf-8-sig") as fh:  # BOM
    w = csv.writer(fh)
    w.writerow(["id", "persona_id", "source_message", "subject", "dimension", "key", "value",
                "operation", "top3_truth_facts", "human_label", "human_note"])
    for a in sample:
        subj = "entity" if a["dimension"] == "relationship" else "self"
        w.writerow([a["id"], a["pid"], msg_text(a["message_id"]), subj, a["dimension"], a["key"],
                    a["value"], op_of(a["node_id"], a["message_id"]), top3(a), "", ""])
with open(OUT / "error_audit_v2_key.csv", "w", newline="", encoding="utf-8") as fh:
    w = csv.writer(fh); w.writerow(["id", "A", "C", "E"])
    for a in sample: w.writerow([a["id"], a["A"], a["C"], a["E"]])

print(f"test={len(test)} A-wrong={len(wrongA)} A-correct_sampled={min(30,len(corrA))} rows={len(sample)}")
print("wrote results/analysis/error_audit_v2.csv (+_key.csv)")
