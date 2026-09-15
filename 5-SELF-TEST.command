#!/bin/bash
# Prove the repairs still work, on damage we already know the answer to.
cd "$(dirname "$0")" || exit 1
PY=$(command -v python3 || command -v python)

if [ "$#" -gt 0 ]; then
    "$PY" "$(dirname "$0")/cadence_selftest.py" "$@"
else
cat <<'TXT'

  SELF-TEST

  Checks that the repair tools still do what they used to do.

  It takes one of your CLEAN clips, makes a small copy, breaks that copy
  in six ways it already knows the answer to, and checks the tools get
  each one right and put it back. Your file is never touched.

  Run this after anything in this folder changes. If every line says
  PASS, nothing that used to work got broken.

  It will NOT find new kinds of damage - only a real clip can do that.

  Drag in ONE clip that 2-DIAGNOSE-ONLY calls CLEAN, then press Return.
  Takes a few minutes.

TXT
    printf "Clean clip: "
    read -r RAW
    RAW="${RAW//\\/}"; RAW="${RAW%\"}"; RAW="${RAW#\"}"
    "$PY" cadence_selftest.py "$RAW"
fi
echo
read -r -p "Press Return to close. "
