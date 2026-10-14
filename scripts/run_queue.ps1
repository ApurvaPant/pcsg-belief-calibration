<#
run_queue.ps1 — overnight priority queue for the PCSG NVIDIA run.

Runs from a PLAIN PowerShell window (no Claude Code). Stages run in priority
order; results land in the final folders and each stage is usable on its own.
Resumable (skips finished stages / personas), survives network drops (backoff in
llm.py) and reboots (re-run to resume). Writes results\QUEUE_STATUS.md at least
every 5 minutes. Graceful pause: create results\PAUSE (or Ctrl+C once) -> the
in-progress persona finishes, state is saved, status shows PAUSED, queue exits.

  Launch:   pwsh -File scripts\run_queue.ps1 -Personas 60
  Resume:   pwsh -File scripts\run_queue.ps1 -Personas 60 -RunId <run_id>
  Pause:    New-Item results\PAUSE -ItemType File      (or press Ctrl+C once)
  Progress: Get-Content results\QUEUE_STATUS.md
  Stop now: press Ctrl+C a second time
  Optional stages 6-7:  add  -IncludeOptional
#>
[CmdletBinding()]
param(
  [int]$Personas = 60,
  [string]$RunId = ("main_" + (Get-Date -Format "yyyyMMdd_HHmmss")),
  [int]$Lme = 108,
  [switch]$IncludeOptional
)
$ErrorActionPreference = "Stop"
$Repo   = Split-Path -Parent $PSScriptRoot
$Backend= Join-Path $Repo "backend"
$Results= Join-Path $Repo "results"
$Py     = Join-Path $Backend ".venv\Scripts\python.exe"
$Status = Join-Path $Results "QUEUE_STATUS.md"
$Pause  = Join-Path $Results "PAUSE"
$QDir   = Join-Path $Results ("runs\" + $RunId + "\queue")
New-Item -ItemType Directory -Force -Path $QDir | Out-Null
$Spec   = "experiments\all.yaml"
$t0     = Get-Date

function Write-Status($stage, $state, $extra) {
  $prog = Join-Path $Results ("runs\" + $RunId + "\progress.json")
  $p = $null; if (Test-Path $prog) { try { $p = Get-Content $prog -Raw | ConvertFrom-Json } catch {} }
  $active = [math]::Round(((Get-Date) - $t0).TotalHours, 2)
  $lines = @(
    "# QUEUE_STATUS — run $RunId",
    "",
    "updated: $(Get-Date -Format s) | active hours: $active | personas: $Personas",
    "current stage: **$stage** — $state",
    ""
  )
  if ($p) {
    $eta = ""
    if ($p.done -gt 0 -and $p.total -gt 0) {
      $per = $p.elapsed_sec / [math]::Max(1,$p.done)
      $eta = [math]::Round($per * ($p.total - $p.done) / 3600.0, 2)
    }
    $r429 = 0.0; if ($p.calls -gt 0) { $r429 = [math]::Round(100.0 * $p.http_429 / $p.calls, 2) }
    $lines += @(
      "replay progress: $($p.done)/$($p.total)  ETA ~${eta} h",
      "tokens: $($p.spent_tokens)  calls: $($p.calls)  retries: $($p.retries)  429-rate: ${r429}%"
    )
  }
  if ($extra) { $lines += @("", $extra) }
  Set-Content -Path $Status -Value ($lines -join "`n") -Encoding utf8
}

function Invoke-Stage($name, [string[]]$pyArgs, [switch]$Figures) {
  $marker = Join-Path $QDir ($name + ".done")
  if (Test-Path $marker) { Write-Host "[queue] $name already done — skip"; return }
  if (Test-Path $Pause)  { Write-Status $name "PAUSED before start"; throw "PAUSED" }
  Write-Host "[queue] starting $name"
  Write-Status $name "running"
  # Run the stage as a background job so we can poll progress + honour PAUSE.
  $job = Start-Job -ScriptBlock {
    param($Backend, $Repo, $Py, $Figures, $pyArgs)
    if ($Figures) { Set-Location $Repo;  & $Py "scripts\make_figures.py" }
    else          { Set-Location $Backend; $env:PYTHONPATH="."; & $Py "-m" "pcsg.run" @pyArgs }
    exit $LASTEXITCODE
  } -ArgumentList $Backend, $Repo, $Py, [bool]$Figures, $pyArgs
  try {
    while ($job.State -eq "Running") {
      Start-Sleep -Seconds 60
      Write-Status $name "running"
      if (Test-Path $Pause) {
        Write-Host "[queue] PAUSE seen — the in-progress persona will finish, then stop."
        Write-Status $name "PAUSING (finishing current persona)"
        Wait-Job $job | Out-Null   # run.py stops itself at the next persona boundary
        break
      }
    }
    Wait-Job $job | Out-Null
    Receive-Job $job
    $code = if ($job.ChildJobs[0].JobStateInfo.State -eq 'Failed') { 1 } else { 0 }
  } finally { Remove-Job $job -Force -ErrorAction SilentlyContinue }
  if (Test-Path $Pause) { Write-Status $name "PAUSED"; throw "PAUSED" }
  if ($code -ne 0) { Write-Status $name "ERROR (see console) — later stages continue"; Write-Host "[queue] $name FAILED (continuing)"; return }
  New-Item -ItemType File -Force -Path $marker | Out-Null
  Write-Status $name "done"
  Write-Host "[queue] $name done"
}

# Ctrl+C -> request a graceful pause (create the PAUSE file).
[Console]::TreatControlCAsInput = $false
$null = Register-EngineEvent PowerShell.Exiting -Action { }  # no-op; cleanup below
try {
  Write-Host "[queue] run_id=$RunId personas=$Personas  (provider from config.yaml; live)"
  # Tier 1: replay + calibration -> T1/T2/F1/RESULTS_SUMMARY(partial)
  Invoke-Stage "tier1_replay_calibration" @("$Spec","--live","--n","$Personas","--run-id","$RunId","--stages","replay,calibration")
  Invoke-Stage "tier1_figures" -Figures
  # Tier 2: QA 4 systems + judge + abstention -> T3/F2  (--n keeps the SAME personas)
  Invoke-Stage "tier2_qa" @("$Spec","--live","--resume","$RunId","--n","$Personas","--stages","replay,qa")
  Invoke-Stage "tier2_figures" -Figures
  # Tier 3: LongMemEval 108 -> T5
  Invoke-Stage "tier3_longmemeval" @("$Spec","--live","--resume","$RunId","--n","$Personas","--lme","$Lme","--stages","replay,lme")
  # Cost sweep -> F3
  Invoke-Stage "cost_sweep" @("$Spec","--live","--resume","$RunId","--n","$Personas","--stages","replay,cost")
  # Final figures + summary
  Invoke-Stage "final_figures" -Figures
  if ($IncludeOptional) {
    Invoke-Stage "model2_gptoss" @("experiments\model2.yaml","--live","--run-id",("model2_"+$RunId),"--stages","replay,calibration")
    Invoke-Stage "effort_ablation" @("experiments\effort_ablation.yaml","--live","--run-id",("ablate_"+$RunId),"--stages","replay,calibration")
    Invoke-Stage "final_figures_opt" -Figures
  }
  Write-Status "ALL" "complete"
  Write-Host "[queue] ALL STAGES COMPLETE"
}
catch {
  if ("$_" -match "PAUSED") { Write-Host "[queue] PAUSED. Remove results\PAUSE and re-run with -RunId $RunId to resume." }
  else { Write-Status "ERROR" "$_"; Write-Host "[queue] queue error: $_" }
}
