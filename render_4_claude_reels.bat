@echo off
REM ===================================================================
REM  Gen Z Capital — Render the 4 top-scored Claude reels to Drive.
REM
REM  Renders, in order:
REM    1. Row 6  — Claude for Small Business (5)
REM    2. Row 10 — Claude builds 3D worlds   (4)
REM    3. Row 7  — Claude for Legal          (3)
REM    4. Row 8  — KPMG hires Claude         (3)
REM  Row 9 (Anthropic + Gates, score 2) is intentionally skipped.
REM
REM  Each render:
REM    - Generates the voiceover MP3 (OpenAI TTS)
REM    - Renders the silent reel (HyperFrames + Pexels video B-roll)
REM    - Muxes the audio in with ffmpeg
REM    - Uploads the final MP4 to Google Drive
REM    - Writes the Drive link back into the Sheet row
REM    - Flips Status to 'Rendered - Review'
REM
REM  Instagram publishing is SKIPPED (the IG perm is still pending).
REM
REM  IMPORTANT: close Claude Code before double-clicking this file.
REM  HyperFrames Chrome workers crash when Claude Code runs in parallel.
REM ===================================================================
cd /d "%~dp0"

echo === Rendering 4 Claude reels to Google Drive ===
echo.

echo [1/4] Claude for Small Business (row 6, score 5)
python scripts\build_and_publish_reel.py --row 6 --to-drive --pexels-video
if errorlevel 1 goto failed

echo.
echo [2/4] Claude builds 3D worlds (row 10, score 4)
python scripts\build_and_publish_reel.py --row 10 --to-drive --pexels-video
if errorlevel 1 goto failed

echo.
echo [3/4] Claude for Legal (row 7, score 3)
python scripts\build_and_publish_reel.py --row 7 --to-drive --pexels-video
if errorlevel 1 goto failed

echo.
echo [4/4] KPMG hires Claude (row 8, score 3)
python scripts\build_and_publish_reel.py --row 8 --to-drive --pexels-video
if errorlevel 1 goto failed

echo.
echo ============================================================
echo  All 4 reels rendered. Drive links are in the Sheet rows.
echo ============================================================
pause
exit /b 0

:failed
echo.
echo ============================================================
echo  RENDER FAILED. Scroll up for the error.
echo  Re-run this .bat — already-rendered rows are now flagged
echo  Status='Rendered - Review' and won't be picked up again
echo  unless you explicitly target them via --row N.
echo ============================================================
pause
exit /b 1
