"""analysis_v2 item 1 -> results/analysis_v2/TEMPLATES.md (OFFLINE, read-only).

Duplicated / templated user messages and whether signal calibration survives dedup.

LINKAGE NOTE: the main run's SQLite DB (which joined each signal to its message text)
was cleaned from TEMP, and labelled.jsonl carries signals+correctness but NOT the
value/message. So per-belief evidence TEXT is recovered by aligning labelled.jsonl to
persona_run.jsonl created/reinforced changes within each (persona,dimension) group that
has matching counts (order-stable); beliefs in count-mismatching groups (refined/
superseded extras) are excluded from the text-linked recompute and the coverage is
reported. This is a sensitivity analysis, stated as such — no silent approximation.
"""
from __future__ import annotations
import json, pathlib, re, sys, collections
import numpy as np
REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "backend"))
from pcsg.analysis import calibration as C
from pcsg.analysis.report import rnd  # version-stable fixed-decimal formatting

RD = REPO / "results" / "runs" / "main_20261015_000553"
OUT = REPO / "results" / "analysis_v2"; OUT.mkdir(parents=True, exist_ok=True)
PR = [json.loads(l) for l in (RD / "persona_run.jsonl").read_text().splitlines()]
LAB = [json.loads(l) for l in (RD / "labelled.jsonl").read_text().splitlines()]
RNG = np.random.default_rng(2026)
_PUNCT = re.compile(r"[^\w\s]")


def norm(t):
    return re.sub(r"\s+", " ", _PUNCT.sub("", (t or "").lower())).strip()


def metrics(p, y):
    p = np.asarray(p, float); y = np.asarray(y, int)
    return {"n": len(y), "acc": rnd(float(y.mean())), "brier": rnd(C.brier(p, y)),
            "auroc": rnd(C.auroc(p, y))}


def main():
    # 1) total vs unique user messages (normalized), overall + per split
    L = ["# TEMPLATES — duplicated / templated messages (analysis_v2)", "",
         "OFFLINE, read-only. See the linkage note in the script header.", "",
         "## Message duplication (normalized: lowercase, strip punctuation, collapse whitespace)", "",
         "| split | total msgs | unique msgs | duplication rate |", "|---|---|---|---|"]
    bysplit = collections.defaultdict(list)
    for m in PR:
        bysplit[m["split"]].append(norm(m["text"]))
    allmsgs = []
    for sp in ("fit", "test", "dev"):
        ms = bysplit.get(sp, []); allmsgs += ms
        u = len(set(ms))
        L.append(f"| {sp} | {len(ms)} | {u} | {rnd(1-u/max(len(ms),1))} |")
    L.append(f"| ALL | {len(allmsgs)} | {len(set(allmsgs))} | {rnd(1-len(set(allmsgs))/max(len(allmsgs),1))} |")

    # message -> set of personas that used it (normalized)
    msg_personas = collections.defaultdict(set)
    for m in PR:
        msg_personas[norm(m["text"])].add(m["persona"])

    # 2) test beliefs whose evidence message appears for ANOTHER persona
    #    beliefs = created/reinforced changes
    cr = collections.defaultdict(list)  # (persona,dim) -> ordered list of {value,text,split}
    for m in PR:
        for ch in m["changes"]:
            if ch.get("action") in ("created", "reinforced"):
                cr[(m["persona"], ch["dimension"])].append(
                    {"value": ch.get("value"), "text": m["text"], "ntext": norm(m["text"]), "split": m["split"]})
    test_beliefs = [b for g, lst in cr.items() for b in lst if b["split"] == "test"]
    shared = sum(1 for b in test_beliefs if len(msg_personas[b["ntext"]]) > 1)
    L += ["", f"## Cross-persona templated evidence",
          f"- test beliefs (created/reinforced): {len(test_beliefs)}",
          f"- **test beliefs whose evidence message text also appears for another persona: {shared}** "
          f"({round(100*shared/max(len(test_beliefs),1),1)}%)"]

    # 3) link signals to evidence text within count-matching (persona,dim) groups
    labg = collections.defaultdict(list)
    for r in LAB:
        labg[(r["persona"], r["dimension"])].append(r)
    linked = []  # {verb,sc,correct_A, ntext, persona}
    matched_groups = 0
    for key, lab_list in labg.items():
        crl = cr.get(key, [])
        if len(crl) == len(lab_list):  # clean order alignment
            matched_groups += 1
            for r, b in zip(lab_list, crl):
                linked.append({"verb": r["verbalized"], "sc": r["self_consistency"],
                               "y": int(r["correct_A"]), "ntext": b["ntext"],
                               "persona": r["persona"], "split": r["split"]})
    linked_test = [x for x in linked if x["split"] == "test"]
    cov = round(len(linked_test) / len([r for r in LAB if r["split"] == "test"]), 3)
    L += ["", "## Signal calibration under dedup (test split; text-linked subset)",
          f"- text-linked test beliefs: {len(linked_test)} of 459 ({int(cov*100)}% coverage; "
          f"{matched_groups}/{len(labg)} (persona,dim) groups cleanly alignable)", ""]

    # evidence-text frequency among linked beliefs
    freq = collections.Counter(x["ntext"] for x in linked_test)
    uniq = [x for x in linked_test if freq[x["ntext"]] == 1]
    L += ["| signal | set | n | acc | Brier | AUROC |", "|---|---|---|---|---|---|"]
    rowjson = {}
    for name, sig in (("verbalized", "verb"), ("self_consistency", "sc")):
        full = metrics([x[sig] for x in linked_test], [x["y"] for x in linked_test])
        uq = metrics([x[sig] for x in uniq], [x["y"] for x in uniq])
        rowjson[name] = {"linked_all": full, "unique_evidence": uq}
        L.append(f"| {name} | text-linked all | {full['n']} | {full['acc']} | {full['brier']} | {full['auroc']} |")
        L.append(f"| {name} | unique-evidence (a) | {uq['n']} | {uq['acc']} | {uq['brier']} | {uq['auroc']} |")

    # (b) cluster bootstrap: cluster = normalized evidence template
    L += ["", "### (b) cluster bootstrap (cluster = normalized evidence template, 2000 reps, seed 2026)",
          "| signal | AUROC [95% CI] | Brier [95% CI] |", "|---|---|---|"]
    clusters = collections.defaultdict(list)
    for i, x in enumerate(linked_test):
        clusters[x["ntext"]].append(i)
    ckeys = list(clusters)
    for name, sig in (("verbalized", "verb"), ("self_consistency", "sc")):
        p = np.array([x[sig] for x in linked_test]); y = np.array([x["y"] for x in linked_test])
        au, br = [], []
        for _ in range(2000):
            samp = RNG.choice(len(ckeys), len(ckeys), replace=True)
            idx = np.concatenate([clusters[ckeys[j]] for j in samp])
            try:
                a = C.auroc(p[idx], y[idx])
                if not np.isnan(a): au.append(a); br.append(C.brier(p[idx], y[idx]))
            except Exception:
                pass
        L.append(f"| {name} | {rnd(C.auroc(p,y))} [{rnd(np.quantile(au,0.025))}, {rnd(np.quantile(au,0.975))}] | "
                 f"{rnd(C.brier(p,y))} [{rnd(np.quantile(br,0.025))}, {rnd(np.quantile(br,0.975))}] |")
    # side-by-side original (full test, no dedup)
    full_test = [r for r in LAB if r["split"] == "test"]
    L += ["", "### Original (full test, no dedup) for comparison",
          "| signal | n | acc | Brier | AUROC |", "|---|---|---|---|---|"]
    for sig, k in (("verbalized", "verbalized"), ("self_consistency", "self_consistency")):
        m = metrics([r[k] for r in full_test], [r["correct_A"] for r in full_test])
        L.append(f"| {sig} | {m['n']} | {m['acc']} | {m['brier']} | {m['auroc']} |")
    (OUT / "TEMPLATES.md").write_text("\n".join(L) + "\n", encoding="utf-8")
    json.dump({"msg_dupe_all": round(1-len(set(allmsgs))/len(allmsgs), 4), "shared_test_beliefs": shared,
               "n_test_beliefs": len(test_beliefs), "linked_cov": cov, "signals": rowjson},
              open(OUT / "_templates.json", "w"), indent=2)
    print(f"[templates] msgs ALL dupe-rate={1-len(set(allmsgs))/len(allmsgs):.4f}; "
          f"shared test-belief evidence={shared}/{len(test_beliefs)}; linked test n={len(linked_test)} (cov {cov})")


if __name__ == "__main__":
    main()
