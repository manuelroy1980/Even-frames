#!/bin/bash
# Double-click me. Or drop files on me from the Terminal.
cd "$(dirname "$0")" || exit 1
PY=$(command -v python3 || command -v python)
if [ -z "$PY" ]; then
    echo "Python is not installed. Run 0-SETUP-CHECK.command first."
    read -r -p "Press Return to close. "; exit 1
fi

if [ "$#" -gt 0 ]; then
    "$PY" "$(dirname "$0")/run_tool.py" fix "$@"
else
cat <<'TXT'

  FIX VIDEO

  Works out what is actually wrong with the clip, then applies the one
  repair that fits it. Skipped frames, held frames, dropped frames and an
  uneven beat are four different faults and they get four different
  treatments.

  The result is always 24 fps, the same number of frames and the same
  length as the original, so it drops straight on your timeline and the
  audio still fits. If a clip cannot be fixed under that rule, it says so
  and leaves the file alone.

  Use the RAW download - not a Topaz file, and not a file this tool has
  already been run on. The repair reads the original defect to work out
  what happened, and an earlier pass has erased it.

  Drag ONE OR MORE video files into this window, then press Return.

TXT
    printf "Files: "
    read -r RAW
    export RAW
    "$PY" run_tool.py fix
fi
echo
echo "If you see an error above, screenshot this window."
echo
read -r -p "Press Return to close. "
