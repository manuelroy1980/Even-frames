@echo off
setlocal
title Self-test
if not "%~1"=="" goto direct
echo.
echo   SELF-TEST
echo.
echo   Checks that the repair tools still do what they used to do.
echo.
echo   It takes one of your CLEAN clips, makes a small copy, breaks that copy
echo   in six ways it already knows the answer to, and checks the tools get
echo   each one right and put it back. Your file is never touched.
echo.
echo   Run this after anything in this folder changes. If every line says
echo   PASS, nothing that used to work got broken.
echo.
echo   It will NOT find new kinds of damage - only a real clip can do that.
echo.
echo   Drag in ONE clip that 2-DIAGNOSE-ONLY calls CLEAN, then press Enter.
echo   Takes a few minutes.
echo.
set "RAW="
set /p RAW=Clean clip:
set RAW=%RAW:"=%
python "%~dp0cadence_selftest.py" "%RAW%"
goto end
:direct
python "%~dp0cadence_selftest.py" %*
:end
echo.
pause
