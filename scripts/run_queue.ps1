<#
run_queue.ps1 - overnight priority queue for the PCSG NVIDIA run.
Windows PowerShell 5.1 compatible. ASCII only. Save as UTF-8 with BOM.

Runs each stage SYNCHRONOUSLY via the call operator with a flat argument array
(no Start-Job array splatting, which silently mangled args before). Each stage
prints its EXACT command, then is verified against results/runs/<id>/last_stage.json
(mode must be live, run_id must match, model calls > 0 for a calling stage) so a
stage can never pass silently again. Figures/summary refuse a dry-run or wrong run.

  Launch:   powershell -ExecutionPolicy Bypass -File scripts\run_queue.ps1 -Personas 60
  Resume:   powershell -ExecutionPolicy Bypass -File scripts\run_queue.ps1 -Personas 60 -RunId <run_id>
  Pause:    New-Item results\PAUSE -ItemType File     (graceful: current persona finishes)
  Progress: Get-Content results\QUEUE_STATUS.md
  Stop now: Ctrl+C (resume re-runs the interrupted persona cleanly; no duplicates)
  Optional stages 6-7:  add  -IncludeOptional
  (A one-DEV-persona live self-test:  -Personas 1 -RunId selftest_<x> ; then delete it.)
#>
[CmdletBinding()]
param(
  [int]$Personas = 60,
  [string]$RunId = ("main_" + (Get-Date -Format "yyyyMMdd_HHmmss")),
  [int]$Lme = 108,
  [switch]$IncludeOptional,
  [switch]$DryRunTest
)
$ErrorActionPreference = "Stop"
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONPATH = "."

$Repo    = Split-Path -Parent $PSScriptRoot
$Backend = Join-Path $Repo "backend"
$Results = Join-Path $Repo "results"
$Py      = Join-Path $Backend ".venv\Scripts\python.exe"
$Status  = Join-Path $Results "QUEUE_STATUS.md"
$Pause   = Join-Path $Results "PAUSE"
$RunDir  = Join-Path $Results ("runs\" + $RunId)
$QDir    = Join-Path $RunDir "queue"
$StateF  = Join-Path $RunDir "queue_state.json"
New-Item -ItemType Directory -Force -Path $QDir | Out-Null
$Mode = if ($DryRunTest) { "--dry-run" } else { "--live" }
$Spec = "experiments\all.yaml"
$t0   = Get-Date
$script:Stages = New-Object System.Collections.Specialized.OrderedDictionary

function Save-State([string]$cur) {
  $obj = @{ run_id = $RunId; personas = $Personas; current = $cur;
            active_hours = [math]::Round(((Get-Date) - $t0).TotalHours, 2);
            stages = @{} }
  foreach ($k in $script:Stages.Keys) { $obj.stages[$k] = $script:Stages[$k] }
  ($obj | ConvertTo-Json -Depth 6) | Set-Content -Path $StateF -Encoding UTF8
  Render-Status
}

function Render-Status {
  if (-not (Test-Path $StateF)) { return }
  $prog = Join-Path $RunDir "progress.json"
  $lines = @()
  $st = Get-Content $StateF -Raw | ConvertFrom-Json
  $lines += "# QUEUE_STATUS - run $($st.run_id)"
  $lines += ""
  $lines += "updated: $(Get-Date -Format s) | active hours: $($st.active_hours) | personas: $($st.personas) | current: $($st.current)"
  $lines += ""
  $lines += "## stages (model calls per stage shown when known)"
  foreach ($k in $st.stages.PSObject.Properties.Name) { $lines += "- $k : $($st.stages.$k)" }
  if (Test-Path $prog) {
    try {
      $p = Get-Content $prog -Raw | ConvertFrom-Json
      $r429 = 0; if ([int]$p.calls -gt 0) { $r429 = [math]::Round(100.0 * [int]$p.http_429 / [int]$p.calls, 2) }
      $eta = ""
      if ([int]$p.done -gt 0 -and [int]$p.total -gt 0) {
        $per = [double]$p.elapsed_sec / [int]$p.done
        $eta = [math]::Round($per * ([int]$p.total - [int]$p.done) / 3600.0, 2)
      }
      $lines += ""
      $lines += "live replay: $($p.done)/$($p.total)  ETA ~${eta} h  tokens $($p.spent_tokens)  calls $($p.calls)  429-rate ${r429}%"
    } catch {}
  }
  Set-Content -Path $Status -Value ($lines -join "`r`n") -Encoding UTF8
}

function Read-LastStage {
  $f = Join-Path $RunDir "last_stage.json"
  if (Test-Path $f) { try { return Get-Content $f -Raw | ConvertFrom-Json } catch { return $null } }
  return $null
}

function Invoke-Run([string]$name, [string[]]$a, [bool]$expectCalls) {
  $marker = Join-Path $QDir ($name + ".done")
  if (Test-Path $marker) { Write-Host "[queue] $name already done - skip"; return }
  if (Test-Path $Pause)  { Write-Host "[queue] PAUSED before $name (remove results\PAUSE to resume)"; Save-State "PAUSED"; exit 0 }
  Write-Host "[queue] STAGE $name"
  Write-Host ("[queue]   cmd: " + $Py + " -m pcsg.run " + ($a -join " "))
  $script:Stages[$name] = "running"; Save-State $name
  Push-Location $Backend
  try { & $Py -m pcsg.run @a; $code = $LASTEXITCODE } finally { Pop-Location }
  if (Test-Path $Pause) { Write-Host "[queue] PAUSED during $name (current persona finished)"; $script:Stages[$name] = "paused"; Save-State "PAUSED"; exit 0 }
  if ($code -ne 0) { $script:Stages[$name] = "ERROR rc=$code"; Save-State $name; Write-Host "[queue] $name FAILED rc=$code - STOPPING"; exit $code }
  # Verify the stage actually ran live for THIS run id and made calls.
  $ls = Read-LastStage
  if ($null -eq $ls)                                     { Write-Host "[queue] GUARD FAIL: no last_stage.json for $RunId"; exit 10 }
  if ((-not $DryRunTest) -and ($ls.mode -ne "live"))     { Write-Host "[queue] GUARD FAIL: $name ran mode=$($ls.mode), expected live"; exit 11 }
  if ($ls.run_id -ne $RunId)                             { Write-Host "[queue] GUARD FAIL: $name run_id=$($ls.run_id) != $RunId"; exit 12 }
  if ($expectCalls -and ([int]$ls.model_calls -le 0))    { Write-Host "[queue] GUARD FAIL: $name made 0 model calls"; exit 13 }
  $script:Stages[$name] = "done (mode=$($ls.mode) personas=$($ls.new_personas)/$($ls.total_personas) calls=$($ls.model_calls) qa_q=$($ls.qa_questions))"
  Write-Host "[queue] $name DONE  mode=$($ls.mode) personas=$($ls.new_personas)/$($ls.total_personas) calls=$($ls.model_calls) qa_q=$($ls.qa_questions)"
  New-Item -ItemType File -Force -Path $marker | Out-Null
  Save-State $name
}

function Invoke-Figures([string]$name) {
  $marker = Join-Path $QDir ($name + ".done")
  if (Test-Path $marker) { Write-Host "[queue] $name already done - skip"; return }
  if (Test-Path $Pause)  { Write-Host "[queue] PAUSED before $name"; Save-State "PAUSED"; exit 0 }
  $figArgs = @("scripts\make_figures.py", "--run-id", $RunId)
  if (-not $DryRunTest) { $figArgs += "--require-live" }
  Write-Host "[queue] STAGE $name"
  Write-Host ("[queue]   cmd: " + $Py + " " + ($figArgs -join " "))
  $script:Stages[$name] = "running"; Save-State $name
  Push-Location $Repo
  try { & $Py @figArgs; $code = $LASTEXITCODE } finally { Pop-Location }
  if ($code -ne 0) { $script:Stages[$name] = "ERROR rc=$code"; Save-State $name; Write-Host "[queue] $name FAILED rc=$code - STOPPING"; exit $code }
  $script:Stages[$name] = "done"; New-Item -ItemType File -Force -Path $marker | Out-Null; Save-State $name
  Write-Host "[queue] $name DONE"
}

Write-Host "[queue] run_id=$RunId personas=$Personas mode=$Mode (provider from config.yaml)"
$N = "$Personas"
# Tier 1: replay + calibration -> figures (T1/T2/F1)
Invoke-Run     "tier1_replay_calibration" @($Spec, $Mode, "--n", $N, "--run-id", $RunId, "--stages", "replay,calibration") $true
Invoke-Figures "tier1_figures"
# Tier 2: QA 4 systems + judge + abstention -> figures (T3/F2)
Invoke-Run     "tier2_qa" @($Spec, $Mode, "--resume", $RunId, "--n", $N, "--stages", "replay,qa") $true
Invoke-Figures "tier2_figures"
# Tier 3: LongMemEval -> T5
Invoke-Run     "tier3_longmemeval" @($Spec, $Mode, "--resume", $RunId, "--n", $N, "--lme", "$Lme", "--stages", "replay,lme") $true
# Cost sweep -> F3  (offline replay: no model calls expected)
Invoke-Run     "cost_sweep" @($Spec, $Mode, "--resume", $RunId, "--n", $N, "--stages", "replay,cost") $false
Invoke-Figures "final_figures"
if ($IncludeOptional) {
  Invoke-Run     "model2_gptoss"   @("experiments\model2.yaml", $Mode, "--run-id", ("model2_" + $RunId), "--stages", "replay,calibration") $true
  Invoke-Run     "effort_ablation" @("experiments\effort_ablation.yaml", $Mode, "--run-id", ("ablate_" + $RunId), "--stages", "replay,calibration") $true
}
$script:Stages["ALL"] = "complete"; Save-State "complete"
Write-Host "[queue] ALL STAGES COMPLETE (run_id=$RunId)"
