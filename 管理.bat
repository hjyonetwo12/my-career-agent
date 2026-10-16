@echo off
cd /d "%~dp0"
echo ============================================
echo   [Career Agent] Starting Kanban Server...
echo   Browser will open http://localhost:5000
echo   (Close this window to stop the server)
echo ============================================
python webapp.py
echo.
pause