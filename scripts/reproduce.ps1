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

Write-Host "== T1-T4 / F1-F3 (make_figures, offline) =="
& $Py (Join-Path $Repo "scripts\make_figures.py") --run-id $RUN

Write-Host "== analysis_v2 (STATS, TEMPLATES, EVIDENCE_V2, HUMAN_AUDIT) =="
foreach ($s in @("sig_recalib.py", "templates.py", "evidence_v2.py", "human_audit.py")) {
  Write-Host "  -> $s"
  & $Py (Join-Path $Repo "scripts\analysis_v2\$s")
}
# ABSTAIN_SC.md is a static skip-with-reason (item 4); nothing to recompute.

Write-Host "== SHA-256 manifest check =="
Set-Location $Repo
& $Py (Join-Path $Repo "scripts\make_manifest.py") --check
if ($LASTEXITCODE -ne 0) { Write-Host "REPRODUCE: MANIFEST CHECK FAILED"; exit 1 }
Write-Host "REPRODUCE: OK (zero API calls)"
