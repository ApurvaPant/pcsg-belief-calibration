# PREFLIGHT_FINAL — NVIDIA run readiness (2026-10-14)

## Verdict: **READY TO LAUNCH (priority queue) — WITH WARNINGS**

The NVIDIA setup is validated and the full chain runs live; the overnight queue is
built, resumable, priority-ordered and pause-able. **But the FULL spec will NOT
complete in 13 h.** The 4-system QA stage is *latency-bound* (one persona's QA did
not finish in ~35 min in-session), so Tier 2 alone exceeds the budget. The priority
queue is designed for exactly this: it front-loads **Tier 1 (the core calibration
result)**, which *does* fit (~4.6 h at 60 personas), then runs Tier 2 / Tier 3
best-effort and **resumes the next night** for the rest. The 80 M-token hard-stop +
per-stage resumability make an overrun safe.

**Blocker to a 13 h full run:** Tier 2 (QA, all four systems) and Tier 3
(LongMemEval ×108) are latency-dominated and together run well beyond 13 h — see
Sizing. Launch is safe; *completeness in one night is not*.

## Models (validated live)
- **Main model, every pipeline role:** `nvidia/nemotron-3-super-120b-a12b` with
  **`chat_template_kwargs: {enable_thinking: false}`** — the exact, fixed setting on every call.
  10-prompt validation: **median reasoning tokens 0**, **median latency 0.9 s**, **0/10 `<think>`**, clean JSON.
  (Also disabled by `{thinking:false}` and `reasoning_effort:"none"`; `/no_think` and `reasoning_effort:"low"` did NOT.)
  DeepSeek V4.1 Flash rejected (timed out >30 s); Qwen not on this key — no auto-fallback (Nemotron is final).
- **Judge (`judge` + `judge_longmemeval`):** `openai/gpt-oss-20b`, `reasoning_effort: "medium"`, final answer only
  (llm.py strips reasoning). LongMemEval per-type grading uses original, attributed functional-equivalent rubric
  templates (source: github.com/xiaowu0162/LongMemEval `src/evaluation/evaluate_qa.py`, MIT) + a structure unit test.
- **Embedding (vector_rag, local, not an API call):** `sentence-transformers/all-MiniLM-L6-v2`, loaded once on CPU.

## Re-validation (STEP 3)
- `pytest` (mock): **141 passed.**  S0 robustness (mock): **19/19, 0 invariant violations.**
- Live extractor eval (`eval_extractor`, 7 cases/split, runs=1): **dev 3/7 (F1 50%)**, **test 3/7 (F1 50%)**.
  Format-compliant (no JSON parse failures; misses are content/key on refinement/negation). END precision 100%,
  destructive-op error 0%. **No prior real-model baseline exists** (earlier provider never ran live; previous
  numbers were the deterministic mock at ~100% on these authored chains), so the ">15-pt vs previous model"
  stop-rule has no real comparator and a real-vs-mock gap is not a regression. **No prompt content was tuned.**
  Tagged `extractor-v2-nvidia`; details in `EXTRACTOR_VERSION.md` v2.

## Pilot (STEP 4) — measured + honest gaps
Ran 1 DEV persona live, full chain, into `results/pilot/` (foreground, rate-limited to 35 RPM).
- **Replay (completed):** 25 turns → **~160 LLM calls/persona** = 25 salience + ~22 frame + ~22 fill +
  ~66 self-consistency (k=3) + 25 chat. Thinking off confirmed (0 reasoning tokens). Replay is
  *rate-bound* (short calls): ~**4.6 min/persona** at 35 RPM.
- **QA (did NOT finish in-session):** the 4-system QA for ONE persona ran **>35 min** and did not complete —
  Nemotron answering with full context + the gpt-oss judge are *latency-bound* (long generations, many seconds
  each), so the 35 RPM cap is not the limiter, wall-time is. This is the dominant cost and the 13 h blocker.
- **Peak RAM:** single process, MiniLM loaded once (~a few hundred MB) + Python + SQLite — modest; the memory
  pressure seen in this session was from *background* shells, not the run. Close other apps on this laptop.

| pilot check | result |
|---|---|
| served model == requested | ✅ (API check: served==requested for Nemotron + gpt-oss) |
| no thinking text in main-model output | ✅ 0/10 in validation; replay reasoning tokens 0 |
| chain runs live (replay → labels → calibration) | ✅ produced live |
| signal coverage (3 signals) | ✅ present on replay signals (STEP-1 fix) |
| 429 rate < 5% | ✅ none observed (rate-limited to 35 RPM) |
| 4-system QA + judge end-to-end | ⚠️ **not completed in-session** (latency-bound; verify live via QUEUE_STATUS) |
| 13 dry-run checks | ✅ previously 13/13 on oracle_mock; live Tier-2/3 unverified end-to-end |

## Sizing to 13 h (35 RPM) — projection + recommendation
Wall-time is **rate-bound for replay** (calls/35/60) but **latency-bound for QA/LongMemEval**.
- **Tier 1 (replay + calibration):** ~160 calls/persona → **~4.6 min/persona**. **60 personas ≈ 4.6 h**; 100 ≈ 7.6 h. (Calibration/figures are offline.)
- **Tier 2 (QA, 4 systems, on the ~40% test split):** latency-bound; 1 persona > 35 min observed (incomplete).
  Even at a ~30 min/persona lower bound, 24 test personas (N=60) ≈ **>12 h alone** → does not fit after Tier 1.
- **Tier 3 (LongMemEval ×108):** each question replays a full ~36-turn haystack (~230 calls) + 4 answers + judge,
  latency-bound → **many hours**; will not start within 13 h after Tiers 1–2.
- **Cost sweep:** offline replay (fast) + a few live cells.

**Recommendation — launch at `-Personas 60` and treat the run as priority-ordered, multi-night:**
- **Night 1 (fits ~13 h):** Tier 1 (≈4.6 h) + Tier-1 figures + cost sweep (offline) → the paper's **core calibration
  + cost-curve results**, plus as much of Tier 2 (QA) as the remaining ~8 h allows (≈15 test personas), all resumable.
- **Night 2 (resume):** finish Tier 2, then Tier 3 (LongMemEval). Re-running the queue with the same `-RunId` resumes.
- **If you need everything in ONE 13 h night,** cut scope (keeping seed 2026, 40/40/20): run QA on a reduced test
  subset and LongMemEval on a subset (e.g. 40 of 108). I did **not** change scope automatically — this is your call.
- **Before committing, watch `QUEUE_STATUS.md` after ~30 min** and after the FIRST Tier-2 persona: its real wall-time
  (which the in-session pilot could not measure to completion) tells you whether to narrow scope.

### Projected hours per stage (N=60; QA/LME are latency-bound lower bounds, verify live)
| stage | projection |
|---|---|
| Tier 1 replay + calibration (+figures) | ~4.6 h |
| Tier 2 QA (24 test personas × 4 systems) | ≥ 12 h (does not fit after Tier 1) — best-effort |
| Tier 3 LongMemEval ×108 | many hours — night 2 |
| cost sweep + final figures | < 0.5 h (offline) |
| **Night-1 guaranteed (Tier 1 + cost + partial QA)** | **~13 h** |

## Launch / pause / resume / progress (plain PowerShell, repo root)
```powershell
# LAUNCH the overnight priority queue (recommended): core result first, rest best-effort + resumable
pwsh -File scripts\run_queue.ps1 -Personas 60

# LAUNCH ONLY the main run (no queue), if you prefer to drive stages yourself:
cd backend ; $env:PYTHONPATH="." ; .\.venv\Scripts\python.exe -m pcsg.run experiments\all.yaml --live --n 60

# PAUSE gracefully (current persona finishes, state saved):
New-Item results\PAUSE -ItemType File        # or press Ctrl+C once in the queue window

# RESUME (skips finished stages/personas; no duplicates):
Remove-Item results\PAUSE -ErrorAction SilentlyContinue
pwsh -File scripts\run_queue.ps1 -Personas 60 -RunId <run_id>

# CHECK PROGRESS (updates ≥ every 5 min):
Get-Content results\QUEUE_STATUS.md

# STOP IMMEDIATELY: press Ctrl+C a second time (or close the window)

# OPTIONAL stages 6–7 (model2 + effort ablation; skipped by default):
pwsh -File scripts\run_queue.ps1 -Personas 60 -RunId <run_id> -IncludeOptional
```
Bash equivalents: `bash scripts/run_queue.sh 60` · `touch results/PAUSE` · `bash scripts/run_queue.sh 60 <run_id>` ·
`cat results/QUEUE_STATUS.md` · `bash scripts/run_queue.sh 60 <run_id> 108 --optional`.

## Healthy numbers to see in QUEUE_STATUS.md after ~30 min
- **stage = tier1_replay_calibration, running**, `replay done` climbing by ~1 every ~4–5 min (so ~6–8 done by 30 min at N=60).
- **429-rate < 5%** (ideally 0 — the shared 35 RPM limiter keeps you under NVIDIA's ~40/min).
- **tokens** climbing steadily and **far below 80 M** (budget hard-stop).
- **no repeated `last error`** lines; a single transient retry is fine (llm.py backs off and continues).
- **ETA** for Tier 1 ≈ 4–5 h at N=60. If the first Tier-2 persona (later) shows ~30 min+, narrow QA/LME scope.

## Not done / caveats
- The in-session pilot's **4-system QA + LongMemEval did not complete** (latency + this laptop's foreground/memory
  limits); their per-item wall-times are projected lower bounds. The queue reports the real rates live.
- Mechanics (stage reuse, pause→resume, no duplicate rows) were validated in **dry-run** (oracle_mock); the live
  chain itself was validated through calibration.
- Key stayed in `.env`, never printed/committed. The full run was **not** launched.
