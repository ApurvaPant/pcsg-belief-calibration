# LME fix-up launcher (2026-10-26). Independent of the Claude Code session.
# Waits for the running lme_gptoss_20261026b process to EXIT (polls every 5 min),
# then runs scripts/lme_fixup.py: re-run failed questions (3 workers, >=60s backoff
# on 429, stop-new 09:10 IST), unwrap {"answer":...}, re-judge blank verdicts,
# regenerate T5_final.md + judge_check_lme.csv. Every step logged to OVERNIGHT_LOG.md.
$ErrorActionPreference = "Continue"
$Repo = Split-Path -Parent $PSScriptRoot
$Py   = Join-Path $Repo "backend\.venv\Scripts\python.exe"
$Log  = Join-Path $Repo "results\OVERNIGHT_LOG.md"
function Log([string]$m) {
  Add-Content -Path $Log -Value ("- {0} [LME-FIXUP] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $m) -Encoding UTF8
  Write-Host $m
}

# A process counts as "the run" if it is python running pcsg.lme_overnight, excluding this fix-up.
function RunAlive {
  $procs = Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" -ErrorAction SilentlyContinue
  foreach ($p in $procs) {
    if ($p.CommandLine -and $p.CommandLine -match "lme_overnight" -and $p.CommandLine -notmatch "lme_fixup") {
      return $true
    }
  }
  return $false
}

Log "fix-up launcher armed; waiting for lme_overnight to exit (poll 5 min)."
# Give it a moment, then poll. Guard with a hard deadline so this never hangs forever.
$deadline = (Get-Date).Date.AddHours(9).AddMinutes(5)   # 09:05 IST: if run still up, proceed anyway (it has a 08:30 hard stop)
while ((RunAlive) -and ((Get-Date) -lt $deadline)) {
  Start-Sleep -Seconds 300
}
if (RunAlive) {
  Log "run still appears alive at $(Get-Date -Format HH:mm) past deadline; proceeding cautiously (it has a 08:30 hard stop)."
} else {
  Log "lme_overnight process has exited; starting fix-up."
}

Set-Location (Join-Path $Repo "backend")
$env:PYTHONPATH = "."
Remove-Item Env:\LLM_PROVIDER -EA SilentlyContinue
Remove-Item Env:\PCSG_OFFLINE -EA SilentlyContinue
& $Py (Join-Path $Repo "scripts\lme_fixup.py")
Log "fix-up launcher finished (python exit $LASTEXITCODE)."
