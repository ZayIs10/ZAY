@echo off
REM ===================================================================
REM  Gen Z Capital - Render Reel to Drive (no Instagram post)
REM  Double-click this file to:
REM    1. Pick the highest-scoring Draft reel from the Google Sheet
REM    2. Generate voiceover + render the video + mux audio
REM    3. Upload the finished MP4 to Google Drive
REM    4. Write the Drive link into the Sheet row
REM  Then review it on your phone and post to Instagram yourself.
REM ===================================================================
cd /d "%~dp0\..\.."
python scripts\build_and_publish_reel.py --pick-best-draft --to-drive --pexels-video
echo.
echo ============================================================
echo  Done. Check the Drive link above (or the Reels sheet row).
echo ============================================================
pause
