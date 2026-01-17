# publish.ps1
# - blog.py 실행해서 글 생성/목록 갱신
# - 변경된 파일이 있을 때만 commit/push
# - 에러 나면 바로 중단

$ErrorActionPreference = "Stop"

Write-Host "== 1) Run blog generator =="

python .\blog.py

Write-Host "== 2) Git status check =="

# 변경사항 있는지 확인
$changes = git status --porcelain

if ([string]::IsNullOrWhiteSpace($changes)) {
    Write-Host "No changes to commit. Done."
    exit 0
}

Write-Host "Changes detected. Proceeding to commit/push..."

Write-Host "== 3) Git add =="
git add .

Write-Host "== 4) Git commit =="
# 커밋 메시지에 날짜/시간 넣기
$timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
git commit -m "auto publish: $timestamp"

Write-Host "== 5) Git push =="
git push

Write-Host "All done! Published successfully."
