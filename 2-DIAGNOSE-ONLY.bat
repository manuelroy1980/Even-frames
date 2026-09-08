@echo off
setlocal
title Diagnose only
if not "%~1"=="" goto direct
echo.
echo   DIAGNOSE ONLY
echo.
echo   Says what is wrong with a clip and what the repair would do, and shows
echo   the evidence it used to decide. Writes nothing at all.
echo.
echo   Use this when you want to know before committing, or when a repair
echo   came out wrong and you want to see what the tool thought it was
echo   looking at.
echo.
echo   Drag ONE OR MORE video files into this window, then press Enter.
echo.
set "RAW="
set /p RAW=Files:
python "%~dp0run_tool.py" check
goto end
:direct
python "%~dp0run_tool.py" check %*
:end
echo.
echo If you see an error above, screenshot this window.
echo.
pause
