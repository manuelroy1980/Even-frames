#!/bin/bash
# Does a repaired clip still line up with its original? Equal length is not
# equal timing.
cd "$(dirname "$0")" || exit 1
PY=$(command -v python3 || command -v python)
echo
echo "  Checks whether a repaired clip still lines up with its original,"
echo "  so the audio will still fit. Equal length is not equal timing."
echo
echo "  Drag the ORIGINAL file in and press Return,"
echo "  then drag the REPAIRED file in and press Return."
echo
printf "Original: "; read -r A
printf "Repaired: "; read -r B
# The Terminal escapes the spaces in a dragged path with backslashes. Taking
# them back out is all that is needed, and it is safer than handing the line to
# eval, which would run anything else that happened to be in the filename.
A="${A//\\/}"; A="${A%\"}"; A="${A#\"}"
B="${B//\\/}"; B="${B%\"}"; B="${B#\"}"
"$PY" timing_check.py "$A" "$B"
echo
read -r -p "Press Return to close. "
