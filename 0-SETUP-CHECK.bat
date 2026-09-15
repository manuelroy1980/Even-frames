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
ffmpeg -hide_banner -filters 2^>^&1 | findstr minterpolate >nul
if errorlevel 1 (
  echo   ^>^>^> THIS FFMPEG HAS NO minterpolate FILTER.
  echo       The tools will fall back to blending, which is worse on fast
  echo       motion. A full build from winget install Gyan.FFmpeg has it.
) else (echo   ^>^>^> minterpolate OK - this ffmpeg can paint invented frames)
echo.
echo If something is missing, see READ-ME-FIRST-WINDOWS.txt
pause
