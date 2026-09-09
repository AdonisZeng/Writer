# Writer 打包脚本（方案 第九章：PyInstaller 目录模式）
# 用法：powershell -ExecutionPolicy Bypass -File build.ps1
# 产物：dist/Writer/Writer.exe + _internal/（依赖内置）

$ErrorActionPreference = "Stop"

# assets/icon.ico 存在时才指定图标（缺失不阻断构建）
$iconArg = @()
if (Test-Path "assets/icon.ico") {
    $iconArg = @("--icon", "assets/icon.ico")
}

python -m PyInstaller --noconsole --onedir `
    --name "Writer" `
    @iconArg `
    --add-data "assets;assets" `
    --add-data "prompts;prompts" `
    --hidden-import "sqlite3" `
    --collect-all "sqlite_vec" `
    --collect-submodules "flet" `
    --clean main.py

Write-Host ""
Write-Host "构建完成：dist/Writer/Writer.exe" -ForegroundColor Green
Write-Host "提示：sqlite-vec 的 DLL 由 --collect-all 携带；novels/ 与 config.json 首次启动自动生成。"
