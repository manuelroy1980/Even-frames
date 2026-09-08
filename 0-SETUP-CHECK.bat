@echo off
title Setup check
echo.
echo Checking whether your computer has the two free programs it needs.
echo.
python --version
if errorlevel 1 (echo   ^>^>^> PYTHON IS MISSING) else (echo   ^>^>^> Python OK)
echo.
ffmpeg -version 2^>^&1 | findstr /B "ffmpeg version"
if errorlevel 1 (echo   ^>^>^> FFMPEG IS MISSING) else (echo   ^>^>^> ffmpeg OK)
echo.
echo If something is missing, see START-HERE.txt
pause
