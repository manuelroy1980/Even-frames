@echo off
setlocal
title Fix video cadence
if not "%~1"=="" goto direct
echo.
echo   FIX VIDEO
echo.
echo   Works out what is actually wrong with the clip, then applies the one
echo   repair that fits it. Skipped frames, held frames and dropped frames are
echo   three different faults and they get three different treatments.
echo.
echo   The result is always 24 fps, the same number of frames and the same
echo   length as the original, so it drops straight on your timeline and the
echo   audio still fits. If a clip cannot be fixed under that rule, it says so
echo   and leaves the file alone.
echo.
echo   Use the RAW download - not a Topaz file, and not a file this tool has
echo   already been run on. The repair reads the original defect to work out
echo   what happened, and an earlier pass has erased it.
echo.
echo   Drag ONE OR MORE video files into this window, then press Enter.
echo.
set "RAW="
set /p RAW=Files:
python "%~dp0run_tool.py" fix
goto end
:direct
python "%~dp0run_tool.py" fix %*
:end
echo.
echo If you see an error above, screenshot this window.
echo.
pause
