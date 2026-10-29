# LongMemEval run (2026-10-26). Memory+answering = openai/gpt-oss-20b (only model returning
# valid 5/5 structured JSON on this endpoint; lightning 0/5, minimax 3/5). Judge =
# minimaxai/minimax-m3.1-flash (different family), official verbatim per-type prompts.
# 4 workers, 90s timeout, 3 retries+jitter, 35 RPM. Order: 6 _abs first, then KU:SSP 2:1
# (seed 2026). GATE: a 2-question end-to-end test must COMPLETE before the full run launches.
# Partial-safe T5 every 10, llm_cache.jsonl + run_progress.log per question. Cutoffs 08:15/08:30.
$ErrorActionPreference = "Continue"
$Repo = Split-Path -Parent $PSScriptRoot
$Py   = Join-Path $Repo "backend\.venv\Scripts\python.exe"
$Results = Join-Path $Repo "results"
$Log  = Join-Path $Results "OVERNIGHT_LOG.md"
$RunId = "lme_gptoss_20261026b"
$Out  = Join-Path $Results "lme\$RunId"
$Ck   = Join-Path $Out "checkpoint.json"
Set-Location (Join-Path $Repo "backend")
$env:PYTHONPATH = "."
Remove-Item Env:\LLM_PROVIDER -EA SilentlyContinue   # real API (gpt-oss + minimax), NOT mock
Remove-Item Env:\PCSG_OFFLINE -EA SilentlyContinue
try { powercfg /change standby-timeout-ac 0 | Out-Null } catch {}
function Log([string]$m) { Add-Content -Path $Log -Value ("- {0} {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $m) -Encoding UTF8; Write-Host $m }
function DoneCount { try { return ((Get-Content $Ck -Raw | ConvertFrom-Json).done).Count } catch { return 0 } }

Log "[LME] start run=$RunId memory=gpt-oss-20b judge=minimax 4w/90s/3retry order=abs-first,2:1 cutoffs 08:15/08:30"

# ---- GATE: 2-question end-to-end test ----
Log "[LME] 2-question end-to-end test (first 2 of ordered list)"
& $Py -m pcsg.lme_overnight --out $Out --select ordered --limit 2 --workers 4
$dc = DoneCount
if ($dc -lt 2) {
  Log "[LME] TEST FAILED: only $dc/2 questions completed end-to-end - NOT launching full run. Inspect $Out\run_progress.log"
  exit 1
}
Log "[LME] TEST PASSED: $dc questions completed. Launching full ordered run."

# ---- FULL run (resumes; skips the 2 test questions) ----
& $Py -m pcsg.lme_overnight --out $Out --select ordered --workers 4 --until "08:15" --hard-until "08:30"
Log "[LME] full run ended (done=$(DoneCount)); see $Out\T5.md"
