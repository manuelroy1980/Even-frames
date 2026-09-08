#!/usr/bin/env python3
"""
cadence_check.py - say what is wrong with a clip, and what the repair would do.
Writes nothing.

  python cadence_check.py FILE [FILE ...] [--json]

This is the FIX button with its hands tied. It calls the same engine, gets the
same verdict and prints the same plan - it simply stops before touching
anything. That is deliberate. The old pair of tools each had their own detector
and their own thresholds, so the checker could say "nothing to repair" about a
clip the repairer would then rebuild at 30 fps, and whichever one you believed,
the other was doing something else. There is now one engine and one answer.
"""
import os, sys, json, argparse, io

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import cadence_core as core
from cadence_core import Unreadable

print = core.print

ADVICE = {
    "CLEAN": ["Nothing to do. Use the clip as it is."],
    "STATIC": ["Nothing to do. The still passages here are the shot, not a fault."],
    "IRREGULAR": ["Nothing to do. The big steps are the motion in the shot.",
                  "An earlier version of these tools would have padded this out. "
                  "It was wrong to."],
    "AMBIGUOUS": ["Not repaired, on purpose. Two explanations fit this clip about "
                  "equally well, and guessing between them is exactly how the wrong "
                  "repair gets applied.",
                  "If you know which it is, run the fix from a command prompt with "
                  "--force-class PAD, --force-class SEAM or --force-class PULLDOWN."],
    "PAD": ["Run FIX VIDEO. It repaints the wasted repeat slots and copies every real "
            "frame through untouched.",
            "Expect the same frame count, the same 24 fps and the same duration."],
    "SEAM": ["Run FIX VIDEO. It evens out a short window around each hitch and leaves "
             "the rest of the clip alone."],
    "PULLDOWN": ["Run FIX VIDEO. It rebuilds the damaged stretch at the rate the "
                 "generator actually ran at, then lays it back down on the 24 fps grid.",
                 "Only the damaged stretch is re-rendered; a clean passage is copied."],
}


def report(path, a):
    print()
    print("=" * 70)
    try:
        v = core.diagnose(path, force=a.force, assume_fps=a.fps,
                          force_class=a.force_class)
    except Unreadable as e:
        print(f"  {os.path.basename(path)}")
        print(f"  SKIPPED: {e}")
        return
    core.describe(v)
    if not v.ok:
        core.log_run(v, "check", outcome="refused")
        return

    print()
    if v.klass in ("CLEAN", "STATIC", "IRREGULAR", "AMBIGUOUS"):
        core.log_run(v, "check", repainted=0, outcome="would leave alone")
        print("  WHAT FIX VIDEO WOULD DO")
        print("    Nothing. It would leave this file alone.")
    else:
        pl = core.plan(v, seam_window=a.seam_window)
        pos, target, real = pl["pos"], pl["target"], pl["real"]
        rp = [pos[i] for i in real]
        n, cursor, repaint, edge = v.frames, 0, 0, []
        for i in range(n):
            p = target[i]
            while cursor + 1 < len(rp) and rp[cursor + 1] <= p + 1e-9:
                cursor += 1
            if abs(p - rp[cursor]) < 1e-6:
                continue
            if cursor + 1 >= len(rp):
                edge.append(i)
            else:
                repaint += 1
        core.plan_summary(v, pl, repaint, fps=a.fps, edge=edge)
        core.log_run(v, "check", repainted=repaint,
                     before=core.evenness(path), outcome="would repair")

    print()
    print("  WHAT TO DO")
    for line in ADVICE.get(v.klass, []):
        for w in core.wrap(line):
            print("    " + w)

    if a.json:
        stem = os.path.splitext(path)[0]
        io.open(f"{stem}_diagnosis.json", "w", encoding="utf-8").write(
            json.dumps(v.as_dict(), indent=2, default=str))
        print(f"    (evidence written to {os.path.basename(stem)}_diagnosis.json)")


ap = argparse.ArgumentParser()
ap.add_argument("inputs", nargs="+")
ap.add_argument("--fps", type=float, default=24.0)
ap.add_argument("--seam-window", type=int, default=8)
ap.add_argument("--force", action="store_true",
                help="diagnose even if the file looks already-repaired")
ap.add_argument("--force-class", choices=["PAD", "SEAM", "PULLDOWN"], default=None)
ap.add_argument("--json", action="store_true",
                help="also write the full evidence beside the file")
a = ap.parse_args()

for p in a.inputs:
    report(p, a)
print()
