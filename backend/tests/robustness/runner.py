"""HTTP-driven robustness runner (§1.2).

Drives the real FastAPI routes end to end via httpx ASGITransport (in-process,
no socket) so the gate, extraction, Q-A resolution and update policy run exactly
as in production — the runner never calls internal pipeline functions. Each user
turn is followed by /debug/wait, /debug/snapshot, the scenario's per-turn
assertions and all global invariants; a final snapshot checks expect_final.

Usage (from backend/):
    PCSG_TEST_MODE=1 PYTHONPATH=. ./.venv/Scripts/python tests/robustness/runner.py \
        --suite all --provider mock --runs 1 --report results/robustness
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
import tempfile
import time
import uuid

HERE = pathlib.Path(__file__).resolve().parent
SCEN_DIR = HERE / "scenarios"
FIX_DIR = HERE / "fixtures"
_WRITE_ACTIONS = {"created", "superseded", "reinforced", "ended", "refined", "linked"}


def _load_scenarios(suite: str, case: str | None, category: str | None, provider: str):
    import yaml
    scen = []
    files = sorted(SCEN_DIR.glob("*.yaml"))
    if suite and suite != "all":
        files = [f for f in files if f.stem == suite]
    for f in files:
        cases = set(case.split(",")) if case else None
        for s in (yaml.safe_load(f.read_text(encoding="utf-8")) or []):
            s.setdefault("category", f.stem)
            if cases and s["id"] not in cases:
                continue
            if category and s["category"] != category:
                continue
            # A scenario's `provider` says where it is meaningful. A mock run
            # skips real-only scenarios (the mock can't represent them); a real
            # run of a specific --case overrides this.
            sp = s.get("provider", "mock")
            if not cases and sp != provider:
                continue
            scen.append(s)
    return scen


def _badge(resp: dict) -> str:
    acts = {c.get("action") for c in resp.get("changes", [])}
    if "retrieval_query" in acts:
        return "RETRIEVAL_QUERY"
    if not resp.get("salient"):
        return "GATED_OUT"
    return "EXTRACTED"


def _writes(resp: dict) -> int:
    return sum(1 for c in resp.get("changes", []) if c.get("action") in _WRITE_ACTIONS)


def _make_judge(provider: str):
    if provider != "real":
        return None
    from app import llm
    sysmsg = (
        "You grade a chatbot REPLY against a RUBRIC. Follow the RUBRIC's own "
        "PASS/FAIL condition exactly — it decides what counts, in BOTH directions.\n"
        "- If the rubric says the reply SHOULD state/mention something, PASS when "
        "the reply conveys that (even inside a longer sentence) and FAIL when it "
        "omits it or states something different. Stating the asked-for fact is "
        "CORRECT here, never a 'fabrication'.\n"
        "- If the rubric says the reply must NOT invent / must abstain, then a "
        "reply that declines, says it does not know / has no record, or asks is a "
        "PASS; FAIL only if it asserts the specific forbidden value. 'I don't have "
        "your phone number' is a valid refusal — PASS it.\n"
        "- Judge meaning, not exact wording. Do not add requirements the rubric "
        "does not state.\n"
        "Answer with exactly 'PASS: <reason>' or 'FAIL: <reason>'.")

    def judge(rubric: str, reply: str):
        # Use the stronger chat model (not the cheap classifier) as the judge: a
        # flaky judge would invalidate every reply_judge result. Still inexpensive.
        r = llm.complete(
            messages=[{"role": "system", "content": sysmsg},
                      {"role": "user", "content": f"RUBRIC: {rubric}\n\nREPLY: {reply}"}],
            model=llm.resolve_model("chat"), temperature=0.0, max_tokens=120)
        t = (r.get("text") or "").strip()
        return t.upper().startswith("PASS"), t[:200]
    return judge


class Client:
    def __init__(self, app):
        # FastAPI's TestClient is a synchronous httpx client that drives the ASGI
        # app in-process through the real routes + middleware — the production
        # HTTP path, without a socket or internal-function calls.
        from fastapi.testclient import TestClient
        self.c = TestClient(app)
        self.token = None

    def _auth(self):
        return {"Authorization": f"Bearer {self.token}"} if self.token else {}

    def reset(self):
        self.c.post("/debug/reset")

    def register(self):
        u = "u" + uuid.uuid4().hex[:10]
        r = self.c.post("/auth/register", json={"username": u, "password": "testpw123"})
        r.raise_for_status()
        self.token = r.json()["token"]

    def chat(self, message, session_id):
        r = self.c.post("/chat", json={"message": message, "session_id": session_id}, headers=self._auth())
        r.raise_for_status()
        return r.json()

    def new_session(self) -> str:
        r = self.c.post("/me/sessions", json={"title": None}, headers=self._auth())
        r.raise_for_status()
        return r.json()["id"]

    def inject_assistant(self, session_id, content):
        self.c.post("/debug/inject_assistant", json={"session_id": session_id, "content": content},
                    headers=self._auth())

    def wait(self, mid):
        self.c.post("/debug/wait", params={"message_id": mid} if mid else {})

    def snapshot(self):
        return self.c.get("/debug/snapshot").json()

    def seed(self, fixture):
        return self.c.post("/debug/seed", json=fixture).json()


def _run_once(client: Client, scen: dict, judge):
    from tests.robustness import assertions as A, invariants as INV
    client.reset()
    client.register()
    results = {"id": scen["id"], "severity": scen.get("severity", "S2"),
               "turns": [], "invariant_violations": [], "assertions": [], "ok": True}

    # optional seed
    setup = scen.get("setup") or {}
    session_id = None
    if setup.get("seed"):
        fix = json.loads((FIX_DIR / pathlib.Path(setup["seed"]).name).read_text(encoding="utf-8"))
        fix.setdefault("user", {})["id"] = _user_id(client)
        seeded = client.seed(fix)
        session_id = seeded.get("session_id")

    prev_snap = client.snapshot()
    for turn in scen.get("turns", []):
        if turn.get("new_session"):
            session_id = None
        if "assistant" in turn:
            if not session_id:  # an assistant-first scenario needs a session to attach to
                session_id = client.new_session()
            client.inject_assistant(session_id, turn["assistant"])
            continue
        if "user" not in turn:
            continue
        resp = client.chat(turn["user"], session_id)
        session_id = resp["session_id"]
        client.wait(resp.get("session_id"))
        curr = client.snapshot()
        tinfo = {"changes": resp.get("changes", []), "salient": resp.get("salient"),
                 "salience_score": resp.get("salience_score"), "reply": resp.get("reply"),
                 "badge": _badge(resp), "writes": _writes(resp)}
        # per-turn assertions
        if turn.get("expect"):
            ctx = {"curr": curr, "prev": prev_snap, "turn": tinfo, "judge": judge}
            for name, ok, detail in A.check_clause(turn["expect"], ctx):
                results["assertions"].append({"turn": turn["user"], "name": name, "ok": ok, "detail": detail})
                if ok is False:
                    results["ok"] = False
        # global invariants
        viols = INV.check_all(prev_snap, curr, tinfo)
        for vmsg in viols:
            results["invariant_violations"].append({"turn": turn["user"], "violation": vmsg})
            results["ok"] = False
        results["turns"].append({"user": turn["user"], "badge": tinfo["badge"], "writes": tinfo["writes"]})
        prev_snap = curr

    # final assertions
    curr = client.snapshot()
    for clause in scen.get("expect_final", []):
        ctx = {"curr": curr, "prev": prev_snap, "turn": {}, "judge": judge}
        for name, ok, detail in A.check_clause(clause, ctx):
            results["assertions"].append({"turn": "FINAL", "name": name, "ok": ok, "detail": detail})
            if ok is False:
                results["ok"] = False
    return results


def _user_id(client: Client) -> str:
    r = client.c.get("/auth/me", headers=client._auth())
    return r.json()["id"]


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--suite", default="all")
    ap.add_argument("--case", default=None)
    ap.add_argument("--category", default=None)
    ap.add_argument("--runs", type=int, default=1)
    ap.add_argument("--provider", choices=["mock", "real"], default="mock")
    ap.add_argument("--no-cache", action="store_true", help="documented no-op: this build has no LLM cache")
    ap.add_argument("--fail-fast", action="store_true")
    ap.add_argument("--report", default="results/robustness")
    args = ap.parse_args(argv)

    os.environ["PCSG_TEST_MODE"] = "1"
    os.environ["LLM_PROVIDER"] = args.provider if args.provider == "mock" else os.environ.get("LLM_PROVIDER", "openrouter")
    db_path = pathlib.Path(tempfile.gettempdir()) / f"pcsg_robust_{uuid.uuid4().hex[:6]}.db"
    os.environ["DB_PATH"] = str(db_path)

    from app.config import settings
    if args.provider == "mock":
        settings.llm.provider = "mock"
    settings.db_path = db_path
    from app import db
    db.init_db()
    from app.main import app

    client = Client(app)
    judge = _make_judge(args.provider)
    scenarios = _load_scenarios(args.suite, args.case, args.category, args.provider)
    if not scenarios:
        print("No scenarios matched.")
        return 1

    run_id = time.strftime("%Y%m%d_%H%M%S")
    outdir = pathlib.Path(args.report) / run_id
    outdir.mkdir(parents=True, exist_ok=True)

    summary = []
    for scen in scenarios:
        passes = 0
        last = None
        for _ in range(args.runs):
            try:
                last = _run_once(client, scen, judge)
            except Exception as e:  # noqa: BLE001
                last = {"id": scen["id"], "ok": False, "error": repr(e),
                        "assertions": [], "invariant_violations": [], "severity": scen.get("severity", "S2")}
            passes += 1 if last.get("ok") else 0
            (outdir / f"{scen['id']}.json").write_text(json.dumps(last, indent=2), encoding="utf-8")
        rate = f"{passes}/{args.runs}"
        status = "PASS" if passes == args.runs else ("FLAKY" if passes else "FAIL")
        fails = [a for a in last.get("assertions", []) if a["ok"] is False]
        nviol = len(last.get("invariant_violations", []))
        summary.append({"id": scen["id"], "severity": scen.get("severity", "S2"),
                        "status": status, "rate": rate, "fail_assertions": len(fails),
                        "invariant_violations": nviol, "error": last.get("error")})
        tag = {"PASS": "ok  ", "FLAKY": "FLK ", "FAIL": "BAD "}[status]
        print(f"{tag}| {scen['id']:5} [{scen.get('severity','S2')}] {rate:>4} | "
              f"fails={len(fails)} inv={nviol}" + (f" ERROR={last.get('error')}" if last.get("error") else ""))
        if fails or nviol:
            for a in fails[:4]:
                print(f"      - {a['turn'][:28]!r:30} {a['name']}: {a['detail'][:90]}")
            for vdict in last.get("invariant_violations", [])[:4]:
                print(f"      - INV {vdict['turn'][:24]!r:26} {vdict['violation'][:90]}")
        if args.fail_fast and status == "FAIL":
            break

    (outdir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    npass = sum(1 for s in summary if s["status"] == "PASS")
    print(f"\n{npass}/{len(summary)} scenarios green  (report: {outdir})")
    return 0 if npass == len(summary) else 2


if __name__ == "__main__":
    sys.exit(main())
