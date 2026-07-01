# Optional cleanup — frees ~250 MB of regenerable scratch.
# Safe to run any time; it ONLY removes intermediate render frames + the loose root recording.
# Your 3 final reel MP4s in renders/ are NOT touched.

$root = Split-Path -Parent $PSScriptRoot
Write-Host "Cleaning scratch in: $root" -ForegroundColor Cyan

# Render scratch (worker frames, captured frames, compiled segments per run)
Get-ChildItem -Path "$root\renders" -Directory -Filter "work-*" -ErrorAction SilentlyContinue | ForEach-Object {
    Write-Host "  Removing $($_.FullName)" -ForegroundColor Yellow
    Remove-Item $_.FullName -Recurse -Force
}

# Loose screen recording in root
Get-ChildItem -Path $root -Filter "Recording*.mp4" -File -ErrorAction SilentlyContinue | ForEach-Object {
    Write-Host "  Removing $($_.FullName)" -ForegroundColor Yellow
    Remove-Item $_.FullName -Force
}

Write-Host "Done." -ForegroundColor Green
