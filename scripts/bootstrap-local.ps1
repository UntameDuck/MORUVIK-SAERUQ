param([string]$PythonPath = "python")
$ErrorActionPreference = "Stop"
$saeruqRoot = Split-Path -Parent $PSScriptRoot
Push-Location -LiteralPath $saeruqRoot
try {
    if (-not (Test-Path -LiteralPath ".venv\Scripts\python.exe")) {
        & $PythonPath -m venv .venv
        if ($LASTEXITCODE -ne 0) { throw "venv 생성 실패" }
    }
    & .\.venv\Scripts\python.exe -m pip install -e ".[dev]"
    if ($LASTEXITCODE -ne 0) { throw "의존성 설치 실패" }
    & .\.venv\Scripts\python.exe -m saeruq doctor
    if ($LASTEXITCODE -ne 0) { throw "환경 진단 실패" }
} finally { Pop-Location }
