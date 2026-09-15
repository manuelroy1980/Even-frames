#!/bin/bash
# Double-click me. Checks whether this Mac has the two free programs the tools need.
cd "$(dirname "$0")" || exit 1
echo
echo "Checking whether this Mac has the two free programs it needs."
echo

if command -v python3 >/dev/null 2>&1; then
    echo "  >>> Python OK   ($(python3 --version 2>&1))"
else
    echo "  >>> PYTHON IS MISSING"
    echo "      Install it with:  brew install python"
    echo "      or from https://www.python.org/downloads/macos/"
fi
echo

if command -v ffmpeg >/dev/null 2>&1; then
    echo "  >>> ffmpeg OK   ($(ffmpeg -version 2>&1 | head -1))"
else
    echo "  >>> FFMPEG IS MISSING"
    echo "      Install it with:  brew install ffmpeg"
fi
echo

# The flow repair leans on ffmpeg's minterpolate filter. A cut-down ffmpeg
# build without it still runs, produces nothing usable, and says nothing about
# why - so it is worth finding out here rather than forty minutes into a batch.
if command -v ffmpeg >/dev/null 2>&1; then
    if ffmpeg -hide_banner -filters 2>/dev/null | grep -q minterpolate; then
        echo "  >>> minterpolate OK - this ffmpeg can paint invented frames"
    else
        echo "  >>> THIS FFMPEG HAS NO minterpolate FILTER."
        echo "      The tools will fall back to blending, which is worse on fast"
        echo "      motion. brew install ffmpeg gives you a full build."
    fi
fi
echo
echo "If something is missing, see READ-ME-FIRST.txt"
echo
read -r -p "Press Return to close. "
