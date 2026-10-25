"""analysis_v2 item 6 -> results/analysis_v2/HUMAN_AUDIT.md (OFFLINE, read-only).

On the 78 human-audited beliefs (annotator raj): AUROC of verbalized & self_consistency
against the human label (lenient: correct|partly=1; strict: correct=1); agreement of
rules A/C/E with the human labels; human-human Cohen's kappa IF a second annotator file
(error_audit_v2_apurva.csv) exists.

Signals (verbalized/self_consistency) are recovered by matching each audit row's
(persona, dimension, value) to the created/reinforced belief in persona_run.jsonl and
aligning to labelled.jsonl within count-matching (persona,dimension) groups (the run DB
that held signal<->sig_id is gone). Match coverage is reported.
"""
from __future__ import annotations
import csv, json, pathlib, sys, collections
import numpy as np
REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "backend"))
from pcsg.analysis import calibration as C
from sklearn.metrics import cohen_kappa_score

RD = REPO / "results" / "runs" / "main_20261015_000553"
A = REPO / "results" / "analysis"
OUT = REPO / "results" / "analysis_v2"; OUT.mkdir(parents=True, exist_ok=True)
PR = [json.loads(l) for l in (RD / "persona_run.jsonl").read_text().splitlines()]
LAB = [json.loads(l) for l in (RD / "labelled.jsonl").read_text().splitlines()]


def rd(f):
    p = A / f
    return list(csv.DictReader(open(p, encoding="utf-8-sig"))) if p.exists() else None


def linked_with_value():
    cr = collections.defaultdict(list)
    for m in PR:
        for ch in m["changes"]:
            if ch.get("action") in ("created", "reinforced"):
                cr[(m["persona"], ch["dimension"])].append(ch.get("value"))
    labg = collections.defaultdict(list)
    for r in LAB:
        labg[(r["persona"], r["dimension"])].append(r)
    # (persona,dim,value) -> list of (verb,sc,correct_A) from cleanly aligned groups
    out = collections.defaultdict(list)
    for key, lab_list in labg.items():
        vals = cr.get(key, [])
        if len(vals) == len(lab_list):
            for r, v in zip(lab_list, vals):
                out[(key[0], key[1], v)].append((r["verbalized"], r["self_consistency"], int(r["correct_A"])))
    return out


def au(p, y):
    p = np.asarray(p, float); y = np.asarray(y, int)
    return round(C.auroc(p, y), 4) if len(set(y.tolist())) > 1 else float("nan")


def main():
    raj = rd("error_audit_v2_raj.csv"); key = {r["id"]: r for r in rd("error_audit_v2_key.csv")}
    lv = linked_with_value()
    rows = []
    unmatched = 0
    for r in raj:
        k = (int(r["persona_id"]), r["dimension"], r["value"])
        cand = lv.get(k)
        kk = key.get(r["id"], {})
        rec = {"id": r["id"], "human": r["human_label"], "A": int(kk.get("A", 0)),
               "C": int(kk.get("C", 0)), "E": int(kk.get("E", 0))}
        if cand:
            rec["verb"] = float(cand[0][0]); rec["sc"] = float(cand[0][1])
        else:
            unmatched += 1
        rows.append(rec)
    matched = [r for r in rows if "verb" in r]
    # human binarizations
    for r in rows:
        r["len"] = 1 if r["human"] in ("correct", "partly") else 0
        r["strict"] = 1 if r["human"] == "correct" else 0
    dist = collections.Counter(r["human"] for r in rows)
    L = ["# HUMAN_AUDIT — analysis_v2 (annotator: raj)", "",
         "OFFLINE, read-only. 78 audited beliefs. Signals matched by (persona,dimension,value) to "
         f"persona_run/labelled (run DB gone); signal match coverage: {len(matched)}/78.", "",
         f"Human label distribution: {dict(dist)}", "",
         "## 1. AUROC of signals vs human label (matched subset)", "",
         "| signal | lenient AUROC (correct|partly=1) | strict AUROC (correct=1) |", "|---|---|---|"]
    for name, k in (("verbalized", "verb"), ("self_consistency", "sc")):
        pl = [r[k] for r in matched]
        L.append(f"| {name} | {au(pl, [r['len'] for r in matched])} | {au(pl, [r['strict'] for r in matched])} |")
    # 2. agreement of rules vs human (lenient human = 'correct-ish')
    L += ["", "## 2. Rule vs human agreement (human lenient: correct|partly = correct)", "",
          "| rule | % agreement with human (lenient) | % agreement (strict) |", "|---|---|---|"]
    for rk in ("A", "C", "E"):
        agl = round(np.mean([r[rk] == r["len"] for r in rows]), 4)
        ags = round(np.mean([r[rk] == r["strict"] for r in rows]), 4)
        L.append(f"| rule {rk} | {agl} | {ags} |")
    # 3. human-human kappa
    apurva = rd("error_audit_v2_apurva.csv")
    if apurva:
        by = {r["id"]: r["human_label"] for r in apurva}
        pairs = [(r["human"], by[r["id"]]) for r in rows if r["id"] in by]
        k = cohen_kappa_score([a for a, _ in pairs], [b for _, b in pairs])
        L += ["", f"## 3. Human-human Cohen's kappa (raj vs apurva): {round(float(k),4)} (n={len(pairs)})"]
    else:
        L += ["", "## 3. Human-human Cohen's kappa", "- **SKIPPED: no second-annotator file "
              "(error_audit_v2_apurva.csv absent); only raj annotated.**"]
    (OUT / "HUMAN_AUDIT.md").write_text("\n".join(L) + "\n", encoding="utf-8")
    json.dump({"n_matched": len(matched), "dist": dict(dist),
               "auroc": {name: {"lenient": au([r[k] for r in matched], [r["len"] for r in matched]),
                                "strict": au([r[k] for r in matched], [r["strict"] for r in matched])}
                         for name, k in (("verbalized", "verb"), ("self_consistency", "sc"))}},
              open(OUT / "_human_audit.json", "w"), indent=2)
    print(f"[human_audit] matched signals {len(matched)}/78; "
          f"SC lenient AUROC={au([r['sc'] for r in matched],[r['len'] for r in matched])}; "
          f"verb lenient AUROC={au([r['verb'] for r in matched],[r['len'] for r in matched])}")


if __name__ == "__main__":
    main()
