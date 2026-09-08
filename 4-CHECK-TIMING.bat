@echo off
setlocal
title Check timing
echo.
echo   Checks whether a repaired clip still lines up with its original,
echo   so the audio will still fit. Equal length is not equal timing.
echo.
echo   Drag the ORIGINAL file in and press Enter,
echo   then drag the REPAIRED file in and press Enter.
echo.
set "A="
set /p A=Original: 
set "B="
set /p B=Repaired: 
set A=%A:"=%
set B=%B:"=%
python "%~dp0timing_check.py" "%A%" "%B%"
echo.
pause
