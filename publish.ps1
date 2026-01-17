# publish.ps1
# - venv의 python.exe로 blog.py를 실행 (venv 활성화 안 해도 됨)
# - 변경된 파일이 있을 때만 commit/push
# - 에러 나면 바로 중단

$ErrorActionPreference = "Stop"

# 이 스크립트가 있는 폴더(=레포 루트)
$ROOT = $PSScriptRoot

# venv python 경로
$PY = Join-Path $ROOT "venv\Scripts\python.exe"

if (!(Test-Path $PY)) {
    Write-Host "ERROR: venv가 없습니다. 아래를 먼저 실행하세요:"
    Write-Host "  python -m venv venv"
    Write-Host "  .\venv\Scripts\Activate.ps1"
    Write-Host "  pip install -r requirements.txt"
    exit 1
}

Write-Host "== 1) Run blog generator (venv python) =="
& $PY (Join-Path $ROOT "blog.py")

Write-Host "== 2) Git status check =="

$changes = git status --porcelain

if ([string]::IsNullOrWhiteSpace($changes)) {
    Write-Host "No changes to commit. Done."
    exit 0
}

Write-Host "Changes detected. Proceeding to commit/push..."

Write-Host "== 3) Git add =="
git add .

Write-Host "== 4) Git commit =="
$timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
git commit -m "auto publish: $timestamp"

Write-Host "== 5) Git push =="
git push

Write-Host "All done! Published successfully."
