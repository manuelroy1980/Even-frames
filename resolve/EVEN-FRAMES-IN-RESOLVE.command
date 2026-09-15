#!/bin/bash
# Even Frames in Resolve - macOS. Requires DaVinci Resolve Studio.
cd "$(dirname "$0")" || exit 1
PY=$(command -v python3 || command -v python)
cat <<'TXT'

  EVEN FRAMES IN RESOLVE

  Reads the clips on a video track of the timeline you have open, repairs
  the ones that need it, and puts each repair on the track directly above
  its original. Nothing on the source track is touched.

  Resolve must be OPEN, with the timeline you want showing, and
  Preferences > System > General > External scripting using = Local.

  This is the slow one. Minutes per clip. Leave it running.

TXT

# Where Resolve keeps its scripting module on macOS. Standard install paths;
# edit these two if yours lives somewhere else.
export RESOLVE_SCRIPT_API="/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting"
export RESOLVE_SCRIPT_LIB="/Applications/DaVinci Resolve/DaVinci Resolve.app/Contents/Libraries/Fusion/fusionscript.so"
export PYTHONPATH="$PYTHONPATH:$RESOLVE_SCRIPT_API/Modules"

if [ ! -f "$RESOLVE_SCRIPT_LIB" ]; then
    echo "  Could not find Resolve at:"
    echo "      $RESOLVE_SCRIPT_LIB"
    echo "  If Resolve is installed somewhere else, edit the two paths near the"
    echo "  top of this file to match."
    echo
    read -r -p "Press Return to close. "
    exit 1
fi

"$PY" ./even_frames_resolve.py "$@"

echo
echo "If you see an error above, screenshot this window."
echo
read -r -p "Press Return to close. "
