@echo off
setlocal
title Even Frames in Resolve
echo.
echo   EVEN FRAMES IN RESOLVE
echo.
echo   Reads the clips on a video track of the timeline you have open, repairs
echo   the ones that need it, and puts each repair on the track directly above
echo   its original. Nothing on the source track is touched.
echo.
echo   Resolve must be OPEN, with the timeline you want showing, and
echo   Preferences ^> System ^> General ^> External scripting using = Local.
echo.
echo   This is the slow one. Minutes per clip. Leave it running.
echo.

REM ---- where Resolve keeps its scripting module (standard Windows install) --
set "RESOLVE_SCRIPT_API=%PROGRAMDATA%\Blackmagic Design\DaVinci Resolve\Support\Developer\Scripting"
set "RESOLVE_SCRIPT_LIB=%PROGRAMFILES%\Blackmagic Design\DaVinci Resolve\fusionscript.dll"
set "PYTHONPATH=%PYTHONPATH%;%RESOLVE_SCRIPT_API%\Modules"

if not exist "%RESOLVE_SCRIPT_LIB%" (
  echo   Could not find Resolve at:
  echo       %RESOLVE_SCRIPT_LIB%
  echo   If Resolve is installed somewhere else, edit the two paths near the top
  echo   of this file to match.
  echo.
  pause
  exit /b 1
)

python "%~dp0even_frames_resolve.py" %*

echo.
echo If you see an error above, screenshot this window.
echo.
pause
