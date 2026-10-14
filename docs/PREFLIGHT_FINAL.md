# PREFLIGHT_FINAL — NVIDIA run readiness (2026-10-14)

## Verdict: **READY TO LAUNCH — recommend `-Personas 60` (projected ~12–13 h, LongMemEval-dominated)**

The NVIDIA setup is validated, the full chain runs live, and the overnight queue is
built, resumable, priority-ordered and pause-able. The 1-persona pilot **exposed and
I fixed two issues** (see Pilot): QA answers were running to the full 1024-token
default and blowing the 60 s per-call timeout, and a timed-out call **crashed the QA
stage** (no per-question guard). After the fixes (answer cap 256 tokens + per-question
try/except in QA and LongMemEval), the full run at **60 personas projects to ~12–13 h**,
dominated by Tier 3 (LongMemEval haystack replay). This is **tight** and the
post-fix QA/LME times are *estimated, not re-measured live* — so the priority queue
front-loads Tier 1 (core calibration, ~4.6 h) and Tier 2 (QA, ~1 h), with Tier 3
best-effort + resumable, and the 80 M-token hard-stop makes any overrun safe.

**Watch the first Tier-2 persona and the first few Tier-3 questions in
`QUEUE_STATUS.md`** to confirm the post-fix rates before trusting the 13 h estimate;
if Tier 3 is slower than projected, it resumes the next night with no lost work.

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

## Pilot (STEP 4) — measured, with two issues found **and fixed**
Ran 1 DEV persona live, full chain, into `results/pilot/` (foreground, 35 RPM). Total wall: **1775 s (~30 min)**.
- **Replay (completed):** 25 turns → **~160 LLM calls/persona** = 25 salience + ~22 frame + ~22 fill +
  ~66 self-consistency (k=3) + 25 chat. Thinking off confirmed (0 reasoning tokens). Replay is *rate-bound*
  (short calls): ~**4.6 min/persona** at 35 RPM. Calibration (Tier 1) produced live.
- **QA — two problems, both fixed:**
  1. QA answers ran to the **1024-token default** and some exceeded the **60 s per-call timeout**; the
     retries also timed out, so QA was pathologically slow. **Fix:** cap the answer generation to 256 tokens
     (`baselines.ANSWER_MAX_TOKENS`; the answer is short JSON) — overriding the model-page default as STEP 2 directs.
  2. A timed-out QA call **raised `httpx.ReadTimeout` and crashed the whole stage** (no per-question guard).
     **Fix:** per-question try/except in QA **and** LongMemEval — a failed question is recorded and the run
     continues (satisfies "a failed question doesn't stop the run / survives a network drop").
- **Peak RAM:** single process, MiniLM loaded once (~a few hundred MB) + Python + SQLite — modest. The memory
  pressure in this session came from *background* shells, not the run. Close other apps on this laptop.

| pilot check | result |
|---|---|
| served model == requested | ✅ (API check: served==requested for Nemotron + gpt-oss) |
| no thinking text in main-model output | ✅ 0/10 in validation; replay reasoning tokens 0 |
| chain runs live (replay → labels → calibration) | ✅ produced live |
| signal coverage (3 signals) | ✅ present on replay signals (STEP-1 fix) |
| 429 rate < 5% | ✅ none observed (rate-limited to 35 RPM) |
| 4-system QA + judge end-to-end | ⚠️ crashed on a timeout in-session → **fixed** (cap + resilience); post-fix QA time estimated, **verify live** |
| failed question/persona doesn't stop the run | ✅ replay (existing) + QA + LongMemEval (fixed this step) |

## Sizing to 13 h (35 RPM) — projection + recommendation
With the answer-length cap, both replay and QA are now **rate-bound** (short calls ≈ the 35 RPM spacing);
LongMemEval is the swing factor because each question replays a whole haystack.
- **Tier 1 (replay + calibration):** ~160 calls/persona → **~4.6 min/persona** (measured). **60 personas ≈ 4.6 h**; 100 ≈ 7.6 h. Calibration/figures offline.
- **Tier 2 (QA, 4 systems, ~40% test split):** ~68 calls/test-persona (≤256-token answers + judge) → ~2 min/persona (post-fix *estimate*). N=60 (24 test) ≈ **~0.8 h**; N=100 (40 test) ≈ ~1.3 h.
- **Tier 3 (LongMemEval ×108):** each question replays a ~36-turn haystack through the pipeline (~90–230 calls) + 4 answers + judge → ~5–7 min/question → **~6–8 h** for 108. The dominant cost; *estimate, verify live*.
- **Cost sweep:** offline replay (fast) + a few live cells.

### Projected hours per stage (N=60; replay measured, QA/LME post-fix estimates)
| stage | projection |
|---|---|
| Tier 1 replay + calibration (+figures) | **~4.6 h** (measured) |
| Tier 2 QA (24 test × 4 systems) + figures | ~0.8 h |
| Tier 3 LongMemEval ×108 | **~6–8 h** (swing factor) |
| cost sweep + final figures | < 0.5 h (offline) |
| **total (N=60)** | **~12–13 h** — tight; fits if LongMemEval lands at the low end |

**Recommendation — launch the priority queue at `-Personas 60`:**
- It produces **Tier 1 (core calibration) first (~4.6 h)**, then QA (~0.8 h), then LongMemEval best-effort. Even if
  LongMemEval overruns 13 h, Tiers 1–2 + cost + figures (the paper's core calibration, baselines and cost-curve
  results) are already written and usable; re-run with the same `-RunId` to finish LongMemEval the next night.
- **Verify the estimate live:** after ~30 min check `QUEUE_STATUS.md`; after the first Tier-2 persona and the first
  2–3 Tier-3 questions, their real wall-times confirm whether the ~12–13 h holds. The in-session pilot measured
  replay but QA/LME times are post-fix projections.
- At **N=100** the run is ~16 h (over) — use 60. To guarantee a one-night finish, optionally shrink LongMemEval
  (e.g. `-Lme 60`) or run QA on fewer test personas; I did **not** change scope automatically.

## Launch / pause / resume / progress — **built-in Windows PowerShell 5.1** (repo root)
`pwsh` (PowerShell 7) is NOT required. Use `powershell.exe` or the `.bat`.
```powershell
# LAUNCH the overnight priority queue (recommended): core result first, rest best-effort + resumable
powershell -ExecutionPolicy Bypass -File scripts\run_queue.ps1 -Personas 60
#   or simply:   scripts\run_queue.bat 60

# LAUNCH ONLY the main run (no queue), if you prefer to drive stages yourself:
cd backend ; $env:PYTHONPATH="." ; $env:PYTHONUTF8="1" ; .\.venv\Scripts\python.exe -m pcsg.run experiments\all.yaml --live --n 60

# PAUSE gracefully (current persona finishes, state saved):
New-Item results\PAUSE -ItemType File

# RESUME (skips finished stages/personas; no duplicates):
Remove-Item results\PAUSE -ErrorAction SilentlyContinue
powershell -ExecutionPolicy Bypass -File scripts\run_queue.ps1 -Personas 60 -RunId <run_id>
#   or:   scripts\run_queue.bat 60 <run_id>

# CHECK PROGRESS (updates at each stage transition + per-persona heartbeat):
Get-Content results\QUEUE_STATUS.md

# STOP IMMEDIATELY: press Ctrl+C (resume re-runs the interrupted persona cleanly; no duplicate rows)

# OPTIONAL stages 6-7 (model2 + effort ablation; skipped by default):
powershell -ExecutionPolicy Bypass -File scripts\run_queue.ps1 -Personas 60 -RunId <run_id> -IncludeOptional
```
Bash equivalents: `bash scripts/run_queue.sh 60` | `touch results/PAUSE` | `bash scripts/run_queue.sh 60 <run_id>` |
`cat results/QUEUE_STATUS.md` | `bash scripts/run_queue.sh 60 <run_id> 108 --optional`.

## Queue fix (2026-10-15) — why Tier 1 silently did nothing, and the guards added
**Root cause:** the queue launched each stage with `Start-Job -ArgumentList ...,$pyArgs`
where `$pyArgs` is an array; PowerShell flattens a trailing array into `-ArgumentList`,
so the job's parameters shifted and the stage never ran `pcsg.run --live` — it fell
through to the figures path and regenerated figures from the OLD dry-run (`20261009_*`).
Tier 1 "finished" in seconds with zero model calls.
**Fixed:** stages now run **synchronously** (`& $Py -m pcsg.run @args`, no job/array splat),
each prints its **exact command**, and each is verified against `results/runs/<id>/last_stage.json`.
A live stage **STOPS the queue** if that file is missing, `mode != live`, the run id differs,
or `model_calls == 0` (for a calling stage); `make_figures` refuses a dry-run or mismatched run
(`--require-live`). Old dry-run outputs are archived under `results/archive_dryrun/`.
**Proven under PS 5.1:** live Tier 1 at `-Personas 1` made **212 real calls**, 1 persona
replayed, 19 assertions stored, labels + `calibration.json` written (~6 min); dry-run full
queue (n=4) completed with figures from the current run; pause -> resume -> idempotent skip verified.

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
