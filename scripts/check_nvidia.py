#!/usr/bin/env python
"""NVIDIA build API health check — DIAGNOSTIC ONLY.

Standalone (httpx only; no pipeline / torch / sentence-transformers imports). Runs
foreground, one call at a time, 30 s timeout, no retries, <=12 calls total. Reads
NVIDIA_API_KEY from backend/.env and NEVER prints it (only loaded? / length /
nvapi- prefix / whitespace). Writes docs/NVIDIA_API_CHECK.md.

    python scripts/check_nvidia.py
"""
from __future__ import annotations

import pathlib
import time

import httpx

ROOT = pathlib.Path(__file__).resolve().parent.parent
ENV = ROOT / "backend" / ".env"
OUT = ROOT / "docs" / "NVIDIA_API_CHECK.md"
BASE = "https://integrate.api.nvidia.com/v1"
TIMEOUT = 30.0
CANDIDATES = [
    "deepseek-ai/deepseek-v4.1-flash",
    "nvidia/deepseek-v4.1-flash",
    "openai/gpt-oss-20b",
    "qwen/qwen3-next-80b-a3b-instruct",
    "nvidia/nemotron-3-super-120b-a12b",
]


def load_key() -> tuple[str, dict]:
    raw = None
    if ENV.exists():
        for ln in ENV.read_text(encoding="utf-8").splitlines():
            if ln.strip().startswith("NVIDIA_API_KEY") and "=" in ln:
                raw = ln.split("=", 1)[1]
                break
    if raw is None:
        return "", {"loaded": False}
    key = raw.strip()
    status = {
        "loaded": bool(key),
        "length": len(key),
        "starts_with_nvapi": key.startswith("nvapi-"),
        "had_whitespace": raw != raw.rstrip("\r\n") or raw.strip() != raw.strip("\r\n ") or raw != raw.strip(),
    }
    return key, status


def headers(key: str) -> dict:
    return {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}


def chat(key: str, model: str, extra: dict | None = None) -> dict:
    payload = {"model": model, "messages": [{"role": "user", "content": "Reply with exactly: OK"}],
               "max_tokens": 50, "temperature": 0}
    if extra:
        payload.update(extra)
    t0 = time.perf_counter()
    try:
        with httpx.Client(timeout=TIMEOUT) as c:
            r = c.post(f"{BASE}/chat/completions", headers=headers(key), json=payload)
        dt = time.perf_counter() - t0
        rec = {"status": r.status_code, "latency": round(dt, 2)}
        if r.status_code == 200:
            d = r.json()
            msg = (d.get("choices") or [{}])[0].get("message", {}) or {}
            content = msg.get("content") or ""
            reasoning = msg.get("reasoning_content") or msg.get("reasoning") or ""
            usage = d.get("usage", {}) or {}
            details = usage.get("completion_tokens_details", {}) or {}
            rec.update({
                "served_model": d.get("model"),
                "reply": content.replace("\n", " ")[:100],
                "reasoning_seen": bool(reasoning) or ("<think>" in content.lower()),
                "prompt_tokens": usage.get("prompt_tokens"),
                "completion_tokens": usage.get("completion_tokens"),
                "reasoning_tokens": details.get("reasoning_tokens"),
                "reasoning_first80": reasoning.replace("\n", " ")[:80] if reasoning else "",
            })
        else:
            rec["error"] = (r.text or "")[:200].replace("\n", " ")
        return rec
    except Exception as e:  # noqa: BLE001
        return {"status": "EXC", "latency": round(time.perf_counter() - t0, 2),
                "error": f"{type(e).__name__}: {str(e)[:160]}"}


def main() -> int:
    key, ks = load_key()
    lines = []  # markdown
    print("=== KEY ===")
    print(ks)
    if not ks.get("loaded"):
        print("NVIDIA_API_KEY not loaded — aborting.")
        OUT.write_text("# NVIDIA API CHECK\n\n**Verdict: NOT WORKING** — NVIDIA_API_KEY not found in backend/.env\n",
                       encoding="utf-8")
        return 1

    # Step 2: model list
    print("\n=== GET /v1/models ===")
    present = {}
    models_status = None
    try:
        with httpx.Client(timeout=TIMEOUT) as c:
            r = c.get(f"{BASE}/models", headers=headers(key))
        models_status = r.status_code
        ids = [m.get("id") for m in (r.json().get("data", []) if r.status_code == 200 else [])]
        for cand in CANDIDATES:
            present[cand] = cand in ids
        print(f"status {models_status}; total ids {len(ids)}")
        for c_, ok in present.items():
            print(f"  {'FOUND' if ok else 'absent':6s} {c_}")
    except Exception as e:  # noqa: BLE001
        models_status = f"EXC {type(e).__name__}: {str(e)[:120]}"
        print(models_status)

    found = [c for c in CANDIDATES if present.get(c)]

    # Step 3: one call per found model
    print("\n=== one chat call per found model ===")
    per_model = {}
    for m in found:
        rec = chat(key, m)
        per_model[m] = rec
        print(f"  {m:42s} {rec.get('status')} {rec.get('latency')}s served={rec.get('served_model')} "
              f"reason={rec.get('reasoning_seen')} rtok={rec.get('reasoning_tokens')} reply={rec.get('reply')!r}")

    # Step 4: DeepSeek reasoning-off variants
    deepseek_id = next((c for c in ("deepseek-ai/deepseek-v4.1-flash", "nvidia/deepseek-v4.1-flash") if present.get(c)), None)
    reasoning_off = {}
    if deepseek_id:
        print(f"\n=== DeepSeek reasoning-off variants on {deepseek_id} ===")
        variants = [
            ("reasoning_effort=none", {"reasoning_effort": "none"}),
            ("reasoning_effort=low", {"reasoning_effort": "low"}),
            ("chat_template_kwargs.thinking=false", {"chat_template_kwargs": {"thinking": False}}),
        ]
        for label, extra in variants:
            rec = chat(key, deepseek_id, extra)
            reasoning_off[label] = rec
            print(f"  {label:38s} {rec.get('status')} {rec.get('latency')}s rtok={rec.get('reasoning_tokens')} "
                  f"reason={rec.get('reasoning_seen')} err={rec.get('error','')[:60]}")

    # Step 5: gpt-oss-20b reasoning_effort medium
    gptoss = {}
    if present.get("openai/gpt-oss-20b"):
        print("\n=== gpt-oss-20b reasoning_effort=medium ===")
        gptoss = chat(key, "openai/gpt-oss-20b", {"reasoning_effort": "medium"})
        sep = (gptoss.get("status") == 200)
        print(f"  status={gptoss.get('status')} rtok={gptoss.get('reasoning_tokens')} "
              f"reasoning_field_present={bool(gptoss.get('reasoning_first80'))} reply={gptoss.get('reply')!r}")

    # Verdict — WORKING only if the PREFERRED main (DeepSeek) and the judge both
    # work; PARTIAL if only a fallback main works; NOT WORKING if no main/judge.
    def ok(rec):
        return isinstance(rec, dict) and rec.get("status") == 200
    preferred_ok = ok(per_model.get("deepseek-ai/deepseek-v4.1-flash", {})) or \
        ok(per_model.get("nvidia/deepseek-v4.1-flash", {}))
    fallback_ok = ok(per_model.get("qwen/qwen3-next-80b-a3b-instruct", {})) or \
        ok(per_model.get("nvidia/nemotron-3-super-120b-a12b", {}))
    have_judge = ok(per_model.get("openai/gpt-oss-20b", {})) or ok(gptoss)
    if (not preferred_ok and not fallback_ok) or not have_judge:
        verdict = "NOT WORKING" if not (preferred_ok or fallback_ok) and not have_judge else "PARTIAL"
    elif preferred_ok and have_judge:
        verdict = "WORKING"
    else:
        verdict = "PARTIAL"  # a fallback main works but the preferred DeepSeek main does not

    # ── markdown report ──
    L = lines.append
    L("# NVIDIA build API — health check\n")
    L(f"**Verdict: {verdict}**\n")
    L("_Diagnostic only; no config/pipeline changes. Foreground, 30 s timeout, no retries._\n")
    L("## Key")
    L(f"- loaded: {ks['loaded']} · length: {ks['length']} · starts with `nvapi-`: {ks['starts_with_nvapi']} · surrounding whitespace: {ks['had_whitespace']}\n")
    L(f"## Model list\n- `GET /v1/models` → status **{models_status}**")
    for c_, ok_ in present.items():
        L(f"  - {'✅ found' if ok_ else '❌ absent'}: `{c_}`")
    L("\n## Per-model chat call (prompt: \"Reply with exactly: OK\")\n")
    L("| model | status | latency | served | reasoning seen | prompt tok | compl tok | reasoning tok | reply (≤100) |")
    L("|---|---|---|---|---|---|---|---|---|")
    for m in found:
        r = per_model[m]
        L(f"| `{m}` | {r.get('status')} | {r.get('latency')}s | {r.get('served_model')} | "
          f"{r.get('reasoning_seen')} | {r.get('prompt_tokens')} | {r.get('completion_tokens')} | "
          f"{r.get('reasoning_tokens')} | {str(r.get('reply',''))[:100]} |")
    if reasoning_off:
        L(f"\n## DeepSeek reasoning-off ({deepseek_id})\n")
        L("| param | status | reasoning tokens | reasoning seen | note |")
        L("|---|---|---|---|---|")
        for label, r in reasoning_off.items():
            note = "accepted" if r.get("status") == 200 else f"rejected: {r.get('error','')[:80]}"
            L(f"| {label} | {r.get('status')} | {r.get('reasoning_tokens')} | {r.get('reasoning_seen')} | {note} |")
        best = [l for l, r in reasoning_off.items() if r.get("status") == 200 and (r.get("reasoning_tokens") or 0) == 0]
        accepted = [l for l, r in reasoning_off.items() if r.get("status") == 200]
        L(f"\n- reasoning-off params ACCEPTED (200): {accepted or 'none'}")
        L(f"- params giving ZERO reasoning tokens: {best or 'none'}")
    if gptoss:
        L(f"\n## gpt-oss-20b (reasoning_effort=medium)\n- status {gptoss.get('status')}, reasoning tokens {gptoss.get('reasoning_tokens')}, "
          f"final answer separable from reasoning: {gptoss.get('status') == 200} (content vs reasoning_content fields)")
    # errors + recommendations
    L("\n## Errors & recommendations")
    code_help = {401: "key problem — check NVIDIA_API_KEY", 403: "model access not accepted — open the model page on build.nvidia.com and accept access",
                 404: "wrong model id", 429: "rate limited — wait and retry",
                 "EXC": "no HTTP response within 30 s (ReadTimeout) — model is reachable but too slow for the 30 s probe; raise the timeout and/or use streaming to test, and if typical latency stays high, fall back to the next main model"}
    any_err = False
    for m, r in per_model.items():
        st = r.get("status")
        if st != 200:
            any_err = True
            L(f"- `{m}`: status {st} — {code_help.get(st, 'see error')}; error: `{str(r.get('error',''))[:160]}`")
    for c_, ok_ in present.items():
        if not ok_:
            L(f"- `{c_}`: absent from /v1/models — likely 404 wrong id, or access not enabled on this key.")
    if not any_err:
        L("- none for the found models.")
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\nVERDICT: {verdict}")
    print(f"report: {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
