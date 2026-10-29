# Overnight runner (2026-10-25). Nemotron 3 Super was withdrawn from the hosted API;
# all new runs use openai/gpt-oss-20b (write-path) + minimaxai/minimax-m3.1-flash (judge/
# paraphraser). ONE process, shared 35 RPM key. Order: STEP 4 -> STEP 1 (LME) -> STEP 3
# (second judge, ADAPTED to LME answers since Tier-2 answers were never persisted) ->
# STEP 2 (paraphrase). Each step: PAUSE-aware, resumable, errors logged + skipped (never
# a tight retry). Hard stop 01:30. Writes OVERNIGHT_SUMMARY.md, commits, tags overnight-v1.
$ErrorActionPreference = "Continue"
$Repo    = "C:\Users\Dell\OneDrive\Desktop\Final Year Project\Chat Interface"
$Backend = Join-Path $Repo "backend"
$Py      = Join-Path $Backend ".venv\Scripts\python.exe"
$Results = Join-Path $Repo "results"
$Log     = Join-Path $Results "OVERNIGHT_LOG.md"
$Pause   = Join-Path $Results "PAUSE"
$RunDate = Get-Date -Format "yyyyMMdd_HHmmss"
$M2Run   = "model2_$RunDate"
$LmeOut  = Join-Path $Results "lme\ov_$RunDate"
$ParaOut = Join-Path $Results "paraphrase\ov_$RunDate"
$JudgeOut= Join-Path $Results "judge_validation"
$HardStop = Get-Date -Hour 1 -Minute 30 -Second 0
if ($HardStop -lt (Get-Date)) { $HardStop = $HardStop.AddDays(1) }

Set-Location $Backend
$env:PYTHONPATH = "."
try { powercfg /change standby-timeout-ac 0 | Out-Null } catch {}

function Log([string]$m) {
  $line = "- {0} {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $m
  Add-Content -Path $Log -Value $line -Encoding UTF8
  Write-Host $line
}
function Guard([string]$step) {
  if (Test-Path $Pause) { Log "[$step] PAUSE present -> stopping before step (resumable)."; return $false }
  if ((Get-Date) -ge $HardStop) { Log "[$step] past hard stop 01:30 -> skipping."; return $false }
  return $true
}

# 12 test personas (seed 2026), saved earlier.
$sel = ((Get-Content (Join-Path $Results "overnight_personas.json") | ConvertFrom-Json).selected_12) -join ","

Log "=== OVERNIGHT START run=$RunDate hardstop=$($HardStop.ToString('HH:mm')) models=gpt-oss-20b+minimax (Nemotron 3 Super withdrawn) ==="

# ---- STEP 4: second extraction model (gpt-oss-20b), 12 personas, k=3 --------------------
if (Guard "STEP4") {
  Log "[STEP4] start: pcsg.run model2_sc (only-personas $sel, replay-workers 12)"
  try {
    & $Py -m pcsg.run "experiments\model2_sc.yaml" --live --run-id $M2Run --only-personas $sel --replay-workers 12 --stages replay,calibration
    & $Py "..\scripts\analysis\model2_compare.py" $M2Run
    Log "[STEP4] done (MODEL2.md written)"
  } catch { Log "[STEP4] ERROR: $($_.Exception.Message)" }
}

# ---- STEP 1: LongMemEval (pilot 5 -> decide full/75), gpt-oss answerers, minimax judge --
if (Guard "STEP1") {
  Log "[STEP1] pilot (5 questions) to size the full set"
  $sel1 = "full"
  try {
    $pilot = & $Py -m pcsg.lme_overnight --out (Join-Path $Results "lme\pilot_$RunDate") --select pilot --workers 18 2>&1
    $pilot | ForEach-Object { Write-Host $_ }
    $proj = ($pilot | Select-String "projected 108 = ([0-9.]+) min").Matches.Groups[1].Value
    if ($proj -and [double]$proj -gt 135) { $sel1 = "75" }
    Log "[STEP1] pilot projected 108 = $proj min -> select=$sel1"
  } catch { Log "[STEP1] pilot ERROR: $($_.Exception.Message); defaulting select=full" }
  if (Guard "STEP1") {
    Log "[STEP1] start LME select=$sel1 until=00:15 workers=18"
    try {
      & $Py -m pcsg.lme_overnight --out $LmeOut --select $sel1 --until "00:15" --workers 18
      Log "[STEP1] done (T5.md written)"
    } catch { Log "[STEP1] ERROR: $($_.Exception.Message)" }
  }
}

# ---- STEP 3: second judge (ADAPTED: cross-judge LME answers; Tier-2 not persisted) ------
if (Guard "STEP3") {
  Log "[STEP3] start second-judge validation on LME answers (gpt-oss vs minimax)"
  try {
    & $Py -m pcsg.judge2_overnight --lme-run $LmeOut --out $JudgeOut --n 150
    Log "[STEP3] done (JUDGE2.md written)"
  } catch { Log "[STEP3] ERROR: $($_.Exception.Message)" }
}

# ---- STEP 2: paraphrase robustness (minimax paraphraser; gpt-oss k=3), hard stop 01:30 --
if (Guard "STEP2") {
  Log "[STEP2] start paraphrase (12 personas, until 01:25, workers 4)"
  try {
    & $Py -m pcsg.paraphrase_overnight --out $ParaOut --model2-run $M2Run --until "01:25" --workers 10
    Log "[STEP2] done (PARAPHRASE.md written)"
  } catch { Log "[STEP2] ERROR: $($_.Exception.Message)" }
}

# ---- FINISH: summary + commit + tag -----------------------------------------------------
try { & $Py -m pcsg.overnight_summary $M2Run $LmeOut $ParaOut $JudgeOut } catch { Log "[summary] ERROR: $($_.Exception.Message)" }
Log "=== OVERNIGHT END ==="
try {
  Set-Location $Repo
  git add results/OVERNIGHT_LOG.md results/OVERNIGHT_SUMMARY.md results/overnight_personas.json results/model2 results/lme results/paraphrase results/judge_validation experiments backend scripts 2>$null
  $msg = @'
overnight(2026-10-25): gpt-oss-20b substitute runs (Nemotron 3 Super withdrawn) - STEP 4/1/3/2 + reports

'@
  $mf = Join-Path $env:TEMP "overnight_commit_msg.txt"
  Set-Content -Path $mf -Value $msg -Encoding UTF8
  git commit -F $mf 2>$null
  git tag -f overnight-v1 2>$null
  Log "[git] committed + tagged overnight-v1"
} catch { Log "[git] ERROR: $($_.Exception.Message)" }
