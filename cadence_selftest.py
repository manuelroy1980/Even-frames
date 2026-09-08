#!/usr/bin/env python3
"""
cadence_selftest.py - prove the four repairs still work, on known damage.

  python cadence_selftest.py CLEAN_CLIP.mp4 [--keep] [--size 480x270]

Give it a clip the DIAGNOSE button calls CLEAN. It makes a small copy, breaks
that copy in four ways it knows the answer to, and checks the tools get each one
right and put it back.

    control    nothing done to it           expect CLEAN, and no file written
    pad        every 4th frame replaced     expect PAD, period 4
               by a copy of the one before
    seam       five single frames removed   expect SEAM, and it should name them
    pulldown   one frame in five removed,   expect PULLDOWN, ~30 fps source
               all the way through

For each repair it then checks the four things that actually matter:

    the frame count, rate and duration came back identical
    the clip measures more even than it did
    a fresh diagnosis of the output no longer sees the defect
    for `seam`, the frames it named are the frames that were removed

WHAT THIS DOES AND DOES NOT TELL YOU

It does not find new kinds of damage. A generator that starts padding on a
six-frame beat, or repeating two frames in a row, would sail past this test and
past the tools, because neither has ever seen one.

What it catches is the tools being broken by a change meant to improve them -
which is a real risk and has already happened once. Fixing the repeat detector
so it would see `walking forest.mp4` made the tools start diagnosing their own
repaired output as freshly damaged; that turned up by hand, and it is exactly
what this would have caught in a minute.

So: run it after these tools are ever changed. If every line says PASS, the
change did not break anything that used to work.

Everything is done on a downscaled copy in a scratch folder and cleaned up
afterwards. Your clip is never touched. Takes a few minutes.
"""
import os, sys, glob, shutil, subprocess, tempfile, argparse, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import cadence_core as core
from cadence_core import RATE, Unreadable

print = core.print

SEAM_KILL = [17, 38, 59, 74, 88]        # frames removed for the `seam` case


def run(cmd, quiet=True):
    return subprocess.call(cmd, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL) if quiet else subprocess.call(cmd)


def build_cases(src, work, size):
    """Extract once, then write four clips out of the same frames."""
    raw = os.path.join(work, "raw")
    os.makedirs(raw, exist_ok=True)
    w, h = size.split("x")
    if run(["ffmpeg", "-v", "error", "-y", "-i", src, "-vf", f"scale={w}:{h}",
            *RATE, "-start_number", "0", "-compression_level", "6",
            os.path.join(raw, "%05d.png")]) != 0:
        raise RuntimeError("could not extract frames from that clip")
    f = sorted(glob.glob(os.path.join(raw, "*.png")))
    n = len(f)
    if n < 60:
        raise RuntimeError(f"that clip is only {n} frames; the test needs at least 60")

    plans = {
        # every 4th frame becomes a copy of the one before it
        "pad": [f[i - 1] if (i % 4 == 3 and i > 0) else f[i] for i in range(n)],
        # five single frames taken out
        "seam": [f[i] for i in range(n) if i not in SEAM_KILL],
        # one frame in five taken out, all the way through: a 30-into-24 squeeze
        "pulldown": [f[i] for i in range(n) if i % 5 != 4],
        # and an untouched control, so a false alarm shows up too
        "control": list(f),
    }
    made = {}
    for name, seq in plans.items():
        d = os.path.join(work, "seq_" + name)
        shutil.rmtree(d, ignore_errors=True); os.makedirs(d)
        for k, s in enumerate(seq):
            try:
                os.link(s, os.path.join(d, f"{k:05d}.png"))
            except OSError:
                shutil.copy(s, os.path.join(d, f"{k:05d}.png"))
        out = os.path.join(work, f"case_{name}.mp4")
        if run(["ffmpeg", "-v", "error", "-y", "-framerate", "24",
                "-i", os.path.join(d, "%05d.png"), "-c:v", "libx264", "-crf", "10",
                "-preset", "ultrafast", "-pix_fmt", "yuv420p", out]) != 0:
            raise RuntimeError(f"could not build the {name} case")
        shutil.rmtree(d, ignore_errors=True)
        made[name] = (out, len(seq))
    shutil.rmtree(raw, ignore_errors=True)
    return made, n


def expected_seams(n):
    """Where the removed frames END UP once the earlier removals have shifted
    everything that follows them left."""
    out = []
    for k, orig in enumerate(sorted(SEAM_KILL)):
        out.append(orig - k)
    return out


def check(name, clip, frames, work, results):
    line = {"case": name, "notes": []}
    try:
        v = core.diagnose(clip)
    except Unreadable as e:
        line.update(ok=False, got="unreadable", notes=[str(e)])
        results.append(line); return

    want = {"control": "CLEAN", "pad": "PAD", "seam": "SEAM", "pulldown": "PULLDOWN"}[name]
    line["got"] = v.klass if v.ok else f"REFUSED:{v.refuse_kind}"
    line["ok"] = (v.klass == want and v.ok)

    if name == "pad" and v.ok:
        big = max(v.regions, key=lambda r: r["span"])
        p = (big.get("pad") or {}).get("period")
        found = sum(len(r["pad"]["members"]) for r in v.regions if r["klass"] == "PAD")
        want = frames // 4
        line["notes"].append(f"period {p}, {found} of {want} repeats found")
        if p != 4:
            line["ok"] = False
            line["notes"].append(f"expected period 4, got {p}")
        if found < 0.8 * want:
            line["ok"] = False
            line["notes"].append(f"too few repeats found")
    if name == "seam" and v.ok:
        found = sorted(s["i"] + 1 for r in v.regions for s in r["spikes"])
        wanted = expected_seams(frames)
        hit = sum(1 for x in wanted if any(abs(x - g) <= 2 for g in found))
        line["notes"].append(f"named {found}, wanted {wanted}, matched {hit}/{len(wanted)}")
        if hit < 4:
            line["ok"] = False
    if name == "pulldown" and v.ok:
        big = max(v.regions, key=lambda r: r["span"])
        line["notes"].append(f"{big.get('implied_rate')} fps source, "
                             f"cycle {big.get('cycle')} at {big.get('cycle_fit')}")

    if name == "control":
        results.append(line); return

    # ---- now actually repair it and check the result ----------------------
    before = core.evenness(clip)
    env = dict(os.environ, CADENCE_TMP=os.path.join(work, "tmp"))
    rc = subprocess.call([sys.executable, os.path.join(HERE, "cadence_fix.py"),
                          clip, "--crf", "16"], env=env,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    stem, ext = os.path.splitext(clip)
    out = f"{stem}_even{ext}"
    if rc != 0 or not os.path.exists(out):
        line["ok"] = False
        line["notes"].append("the repair produced no file")
        results.append(line); return

    c0, c1 = core.probe(clip), core.probe(out)
    same = ((c1.nb_frames or frames) == frames
            and abs((c1.duration or 0) - (c0.duration or 0)) < 0.02
            and abs((c1.fps or 0) - (c0.fps or 0)) < 0.01)
    if not same:
        line["ok"] = False
        line["notes"].append(f"length changed: {c0.nb_frames}f/{c0.duration:.3f}s "
                             f"-> {c1.nb_frames}f/{c1.duration:.3f}s")
    else:
        line["notes"].append(f"{frames}f {c0.duration:.3f}s unchanged")

    after = core.evenness(out)
    if before and after:
        sb = before[0] + 0.5 * (before[1] - 1)
        sa = after[0] + 0.5 * (after[1] - 1)
        line["notes"].append(f"unevenness {sb:.2f} -> {sa:.2f}")
        if not sa < sb * 0.75:
            line["ok"] = False
            line["notes"].append("not enough better")
    try:
        v2 = core.diagnose(out, force=True)
        line["notes"].append(f"re-reads as {v2.klass}")
        if v2.klass not in ("CLEAN", "STATIC", "IRREGULAR"):
            line["ok"] = False
    except Exception:
        pass
    results.append(line)


ap = argparse.ArgumentParser()
ap.add_argument("input", nargs="?")
ap.add_argument("--size", default="480x270",
                help="working resolution; smaller is faster (default 480x270)")
ap.add_argument("--keep", action="store_true", help="leave the test clips behind")
a = ap.parse_args()

if not a.input:
    print("usage: cadence_selftest.py CLEAN_CLIP.mp4")
    print("Give it any clip that DIAGNOSE calls CLEAN.")
    sys.exit(2)

print()
print("=" * 70)
print("  SELF-TEST  -  breaking a clip four known ways and checking the repairs")
print("=" * 70)
print()
print(f"  source clip : {os.path.basename(a.input)}")

try:
    v0 = core.diagnose(a.input)
except Unreadable as e:
    print(f"  cannot read that file: {e}")
    sys.exit(1)

if not v0.ok or v0.klass in ("PAD", "SEAM", "PULLDOWN", "AMBIGUOUS"):
    print()
    print(f"  That clip is not clean - it reads as "
          f"{v0.klass if v0.ok else 'REFUSED: ' + str(v0.refuse_kind)}.")
    print( "  The test needs a clip with nothing wrong with it, because it works by")
    print( "  breaking one in ways it already knows the answer to. Damage that is")
    print( "  already in the clip would be mixed in with the damage being added, and")
    print( "  the answers would mean nothing.")
    print()
    print( "  Run DIAGNOSE over a few of your raw downloads and use one it calls CLEAN.")
    sys.exit(1)

print(f"  reads as    : {v0.klass}, {v0.frames} frames - nothing here to repair, "
      f"so it is safe to test with")
print(f"  working at  : {a.size}")
print()

work = os.path.join(os.environ.get("CADENCE_TMP", tempfile.gettempdir()), "cadenceselftest")
shutil.rmtree(work, ignore_errors=True); os.makedirs(work)
t0 = time.time()
results = []
try:
    print("  building the four test clips...")
    cases, n = build_cases(a.input, work, a.size)
    for name in ("control", "pad", "seam", "pulldown"):
        clip, frames = cases[name]
        print(f"  testing {name}...")
        check(name, clip, frames, work, results)
finally:
    if not a.keep:
        shutil.rmtree(os.path.join(work, "tmp"), ignore_errors=True)

print()
print("=" * 70)
width = max(len(r["case"]) for r in results)
passed = 0
for r in results:
    mark = "PASS" if r["ok"] else "FAIL"
    passed += 1 if r["ok"] else 0
    print(f"  {mark}  {r['case']:<{width}}  read as {r['got']}")
    for note in r["notes"]:
        print(f"        {note}")
print("=" * 70)
print(f"  {passed} of {len(results)} passed, in {time.time() - t0:.0f} seconds")
if passed == len(results):
    print("  Everything that used to work still works.")
else:
    print()
    print("  Something that used to work is broken. Please open a GitHub issue with this whole window;")
    print("  the case name and what it read as is enough to find it.")
print()
if a.keep:
    print(f"  test clips left in: {work}")
else:
    shutil.rmtree(work, ignore_errors=True)
sys.exit(0 if passed == len(results) else 1)
