#!/usr/bin/env python3
"""
run_tool.py - the launcher the .bat icons call. Does the file-list parsing that
Windows batch is bad at: takes paths as arguments, or as the raw dragged-in line
in the RAW environment variable, expands wildcards, runs the right tool on each
file in turn and prints a summary at the end.

  python run_tool.py fix|check [files...]

There are two verbs now, and they are the same engine:

    fix     diagnose, then apply the one repair the clip has earned
    check   diagnose only, write nothing

The old verbs (dup, seam, held, cadence) are gone along with the scripts behind
them. They were four tools with four different detectors, and choosing between
them by hand is how the wrong repair kept getting applied to the right clip.
"""
import os, sys, glob, shlex, subprocess, functools

print = functools.partial(print, flush=True)

TOOLS = {
    "fix":   ("cadence_fix.py", ["--crf", "14"], "Fixing"),
    "check": ("cadence_check.py", [], "Checking"),
}

RETIRED = {
    "dup": "fix_cadence.py", "seam": "seam_fix.py",
    "held": "rebuild_held.py", "cadence": "cadence_fix.py",
}

here = os.path.dirname(os.path.abspath(__file__))
verb = sys.argv[1] if len(sys.argv) > 1 else ""
if verb in RETIRED:
    print()
    print(f"  '{verb}' has been retired. Use 1-FIX-VIDEO.bat instead - it works out")
    print( "  which repair this clip needs and applies that one.")
    print()
    verb = "fix"
if verb not in TOOLS:
    print("usage: run_tool.py fix|check [files...]")
    sys.exit(2)
script, extra, gerund = TOOLS[verb]

args = sys.argv[2:]
if not args:
    raw = os.environ.get("RAW", "").strip()
    if raw:
        # Windows and macOS hand you a dragged path in two different shapes.
        # Windows quotes anything with a space in it and leaves the path alone;
        # the Terminal escapes the space with a backslash instead. Parsing one
        # the other's way splits "My Clip.mp4" into two files that do not exist,
        # which is a baffling error message for something the user did right.
        try:
            args = shlex.split(raw, posix=(os.name != "nt"))
        except ValueError:
            args = raw.split()
        args = [x.strip('"').strip("'") for x in args if x.strip('"').strip("'")]

files = []
for x in args:
    hits = sorted(glob.glob(x))
    files.extend(hits if hits else [x])
seen, keep = set(), []
for f in files:
    k = os.path.abspath(f).lower()
    if k in seen:
        continue
    seen.add(k)
    keep.append(f)
files = keep

if not files:
    print()
    print("  No files were given.")
    print("  Run it again and drag your video files into the window, then press Enter.")
    sys.exit(1)

print()
print(f"  {len(files)} file(s) queued.  {gerund}.")
print("  Leave this window open until it says ALL DONE.")

bad = []
for i, f in enumerate(files, 1):
    print()
    print("#" * 70)
    print(f"  {i} of {len(files)}   {os.path.basename(f)}")
    print("#" * 70)
    if not os.path.isfile(f):
        print("   >>> FILE NOT FOUND - skipped")
        bad.append((f, "not found")); continue
    try:
        rc = subprocess.call([sys.executable, os.path.join(here, script), f] + extra)
    except Exception as e:
        print("   >>> COULD NOT START:", e)
        bad.append((f, "could not start")); continue
    if rc != 0:
        print("   >>> THIS ONE DID NOT PRODUCE A USABLE FILE - read the lines above")
        bad.append((f, "failed, or the result was rejected by its own checks"))

print()
print("=" * 26 + " ALL DONE " + "=" * 26)
print(f"  {len(files)} file(s) processed.  Problems: {len(bad)}")
for f, why in bad:
    print(f"    - {os.path.basename(f)}: {why}")
if verb == "fix":
    print("  Repaired files are saved next to their originals, named _even.")
    print("  Each one has a _even_REPORT.txt beside it saying what was done.")
    if bad:
        print()
        print("  Anything named _even_REJECTED FAILED ITS OWN CHECKS. Keep the")
        print("  original; that file is kept only so you can see what went wrong.")
print()
sys.exit(1 if bad else 0)
