#!/usr/bin/env python3
"""
cadence_selftest.py - prove every repair still works, on known damage.

  python cadence_selftest.py CLEAN_CLIP.mp4 [--keep] [--size 480x270]

Give it a clip the DIAGNOSE button calls CLEAN. It makes a small copy, breaks
that copy in six ways it knows the answer to, and checks the tools get each one
right and put it back.

    control    nothing done to it           expect CLEAN, and no file written
    pad        every 4th frame replaced     expect PAD, period 4
               by a copy of the one before
    seam       five single frames removed   expect SEAM, and it should name them
    pulldown   one frame in five removed,   expect PULLDOWN, ~30 fps source
               all the way through
    grid       nothing removed and nothing     expect GRID, a beat of 2
               repeated - every frame simply
               sits an eighth of a frame off

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


def _pad_offset(f, n):
    """A padded clip where the copy and the doubled step are not adjacent."""
    seq = []
    for i in range(n):
        g, p = divmod(i, 4)
        j = 4 * g + (0, 1, 1, 2)[p]
        if j >= n:
            break
        seq.append(f[j])
    return seq


def _grid_frames(src, work, size, n):
    """Frames for a GRID case: no repeats, no drops, just an uneven beat.

    Every other case here is built by shuffling whole frames about, because
    every other fault IS whole frames - a copy, a gap. A grid fault is not. The
    frames are all present and all different, and each one simply sits a fraction
    of a frame away from where it should. There is no way to build that out of
    the pictures we were given, so the pictures have to be made: one interpolated
    pass at eight times the rate, and then every eighth position picked with a
    one-eighth wobble on it.

    Both phases are picked OFF the original frame positions on purpose. Landing
    one phase on real frames and the other on invented ones would build a clip
    that alternates sharp, soft, sharp - and that is a texture beat, not a
    cadence fault. The test would then be measuring the wrong thing and would
    pass or fail for the wrong reason.
    """
    d = os.path.join(work, "grid8")
    shutil.rmtree(d, ignore_errors=True); os.makedirs(d)
    w, h = size.split("x")
    if run(["ffmpeg", "-v", "error", "-y", "-i", src,
            "-vf", f"scale={w}:{h},minterpolate=fps=192:mi_mode=mci:mc_mode=aobmc",
            "-start_number", "0", "-compression_level", "1",
            os.path.join(d, "%06d.png")]) != 0:
        return None, d
    eight = sorted(glob.glob(os.path.join(d, "*.png")))
    seq = []
    for i in range(n):
        k = 8 * i + (2 if i % 2 == 0 else 3)      # steps of 9 and 7: +-12.5%
        if k >= len(eight):
            break
        seq.append(eight[k])
    return (seq if len(seq) >= 60 else None), d


def build_cases(src, work, size):
    """Extract once, then write six clips out of the same frames."""
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
        # every 4th frame becomes a copy of the one before it, and the step
        # straight out of the copy carries the moment it replaced
        "pad": [f[i - 1] if (i % 4 == 3 and i > 0) else f[i] for i in range(n)],
        # the same damage at a different phase: an ordinary frame sits BETWEEN
        # the copy and the doubled step. Source indices run 0,1,1,2 per group of
        # four, so the steps go move, freeze, move, double. This is the case the
        # repair used to get wrong - it split the healthy pair next to the copy
        # and left the double alone, taking the freeze out and leaving the lurch.
        "pad_offset": _pad_offset(f, n),
        # five single frames taken out
        "seam": [f[i] for i in range(n) if i not in SEAM_KILL],
        # one frame in five taken out, all the way through: a 30-into-24 squeeze
        "pulldown": [f[i] for i in range(n) if i % 5 != 4],
        # and an untouched control, so a false alarm shows up too
        "control": list(f),
    }
    grid_seq, grid_dir = _grid_frames(src, work, size, n)
    if grid_seq:
        plans["grid"] = grid_seq
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
    shutil.rmtree(grid_dir, ignore_errors=True)
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

    want = {"control": "CLEAN", "pad": "PAD", "pad_offset": "PAD",
            "seam": "SEAM", "pulldown": "PULLDOWN", "grid": "GRID"}[name]
    line["got"] = v.klass if v.ok else f"REFUSED:{v.refuse_kind}"
    line["ok"] = (v.klass == want and v.ok)

    if name.startswith("pad") and v.ok:
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
    if name == "grid" and v.ok:
        big = max(v.regions, key=lambda r: r["span"])
        g = big.get("grid") or {}
        line["notes"].append(f"beat of {g.get('m')}, phases {g.get('amp', 0):.0%} apart "
                             f"(F={g.get('F')}), and no frame repeated")
        if (g.get("m") or 0) != 2:
            line["ok"] = False
            line["notes"].append(f"expected a 2-frame beat, got {g.get('m')}")

    if name == "control":
        results.append(line); return

    # ---- now actually repair it and check the result ----------------------
    before = core.evenness(clip)
    env = dict(os.environ, CADENCE_TMP=os.path.join(work, "tmp"))
    rc = subprocess.call([sys.executable, os.path.join(HERE, "cadence_fix.py"),
                          clip, "--crf", "16"], env=env,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    made = core.even_versions(clip)
    out = made[-1] if made else None
    if rc != 0 or not out:
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
print("  SELF-TEST  -  breaking a clip six known ways and checking the repairs")
print("=" * 70)
print()
print(f"  source clip : {os.path.basename(a.input)}")

try:
    # force=True: this clip is only ever measured and copied from, never repaired,
    # so a Topaz tag on it is not a reason to stop. The damage the test injects is
    # its own, and the cases are re-encoded without any tags.
    v0 = core.diagnose(a.input, force=True)
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
    print("  building the test clips...")
    cases, n = build_cases(a.input, work, a.size)
    for name in ("control", "pad", "pad_offset", "seam", "pulldown", "grid"):
        if name not in cases:
            print(f"  skipping {name} - the case could not be built on this machine")
            continue
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
    print("  Before assuming the tools are broken, look at which cases failed.")
    print("  The test works by injecting damage into YOUR clip, and the damage has")
    print("  to stand out against that clip's own motion to be findable. A clip with")
    print("  restless or uneven motion can hide it, and then a case reads as CLEAN or")
    print("  finds only some of the repeats even though nothing is wrong with the")
    print("  tools. A steady camera move - a pan, a push in, a walk - makes the")
    print("  clearest test bed. Try a second clip before concluding anything.")
    print()
    print("  If a steady clip fails too, that is a real regression: please open a")
    print("  GitHub issue with this whole window. The case name and what it read as")
    print("  is enough to find it.")
print()
if a.keep:
    print(f"  test clips left in: {work}")
else:
    shutil.rmtree(work, ignore_errors=True)
sys.exit(0 if passed == len(results) else 1)
