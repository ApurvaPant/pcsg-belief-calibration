# Reproduce EVERY paper table/figure + analysis_v2 outputs from the run folders with
# ZERO API calls, then SHA-256 check against results/MANIFEST.json. (Windows PowerShell)
#   powershell -ExecutionPolicy Bypass -File scripts\reproduce.ps1
$ErrorActionPreference = "Stop"
$Repo = Split-Path -Parent $PSScriptRoot
$Py = Join-Path $Repo "backend\.venv\Scripts\python.exe"
$env:PCSG_OFFLINE = "1"          # any non-mock LLM call now raises
$env:PYTHONPATH = "."
Set-Location (Join-Path $Repo "backend")
$RUN = "main_20261015_000553"

# $ErrorActionPreference='Stop' does NOT abort on a native exe's non-zero exit, so
# check $LASTEXITCODE after every python call and stop at the first failure.
function Invoke-Step($Label, $ScriptPath, $Rest) {
  Write-Host $Label
  & $Py $ScriptPath @Rest
  if ($LASTEXITCODE -ne 0) {
    Write-Host "REPRODUCE: FAILED at '$Label' (exit $LASTEXITCODE)"
    exit 1
  }
}

Invoke-Step "== T1-T4 / F1-F3 (make_figures, offline) ==" (Join-Path $Repo "scripts\make_figures.py") @("--run-id", $RUN)

Write-Host "== analysis_v2 (STATS, TEMPLATES, EVIDENCE_V2, HUMAN_AUDIT) =="
foreach ($s in @("sig_recalib.py", "templates.py", "evidence_v2.py", "human_audit.py")) {
  Invoke-Step "  -> $s" (Join-Path $Repo "scripts\analysis_v2\$s") @()
}
# ABSTAIN_SC.md is a static skip-with-reason (item 4); nothing to recompute.

Write-Host "== SHA-256 manifest check =="
Set-Location $Repo
Invoke-Step "  -> make_manifest.py --check" (Join-Path $Repo "scripts\make_manifest.py") @("--check")
Write-Host "REPRODUCE: OK (zero API calls)"
