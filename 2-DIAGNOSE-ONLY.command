#!/bin/bash
# Double-click me. Says what is wrong and writes nothing at all.
cd "$(dirname "$0")" || exit 1
PY=$(command -v python3 || command -v python)
if [ -z "$PY" ]; then
    echo "Python is not installed. Run 0-SETUP-CHECK.command first."
    read -r -p "Press Return to close. "; exit 1
fi

if [ "$#" -gt 0 ]; then
    "$PY" "$(dirname "$0")/run_tool.py" check "$@"
else
cat <<'TXT'

  DIAGNOSE ONLY

  Says what is wrong with a clip and what the repair would do, and shows
  the evidence it used to decide. Writes nothing at all.

  Use this when you want to know before committing, or when a repair
  came out wrong and you want to see what the tool thought it was
  looking at.

  Drag ONE OR MORE video files into this window, then press Return.

TXT
    printf "Files: "
    read -r RAW
    export RAW
    "$PY" run_tool.py check
fi
echo
echo "If you see an error above, screenshot this window."
echo
read -r -p "Press Return to close. "
