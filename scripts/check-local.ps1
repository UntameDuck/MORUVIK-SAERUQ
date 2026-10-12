$ErrorActionPreference = "Stop"
$saeruqRoot = Split-Path -Parent $PSScriptRoot
Push-Location -LiteralPath $saeruqRoot
try {
    $saeruqPython = Join-Path $saeruqRoot ".venv\Scripts\python.exe"
    if (-not (Test-Path -LiteralPath $saeruqPython)) { throw "bootstrap-local.ps1을 먼저 실행하세요." }
    & $saeruqPython -m pytest -q
    if ($LASTEXITCODE -ne 0) { throw "테스트 실패" }
    & $saeruqPython -m saeruq doctor
    if ($LASTEXITCODE -ne 0) { throw "환경 진단 실패" }
    & $saeruqPython -m saeruq fixtures
    if ($LASTEXITCODE -ne 0) { throw "평가 입력 준비 실패" }
    $saeruqReport = "runs/local-check-" + [guid]::NewGuid().ToString("N") + ".json"
    & $saeruqPython -m saeruq evaluate --backend mock --manifest datasets/fixtures/evaluation.json --report $saeruqReport
    if ($LASTEXITCODE -ne 0) { throw "샘플 평가 실패" }
} finally { Pop-Location }
