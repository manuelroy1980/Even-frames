#!/usr/bin/env python3
"""
cadence_fix.py - diagnose a clip with cadence_core, then apply the ONE repair
that clip has earned.

  python cadence_fix.py FILE [FILE ...] [options]

The rule this file exists to keep:

    THE OUTPUT IS ALWAYS 24 fps, ALWAYS THE SAME NUMBER OF FRAMES, AND ALWAYS
    THE SAME DURATION AS THE INPUT.

Never a 25 or 30 fps file to conform later, never a few percent longer with a
retime note, never a silent audio drift. If a clip cannot be repaired under that
rule, this tool says so and leaves it alone. That is the whole point: a file
that comes out of here drops straight onto a 24 fps timeline beside its audio.

How the repair works, once for every class
------------------------------------------
cadence_core hands over two numbers per frame: where the frame IS on the motion
timeline, and where it SHOULD be. Making that true is one operation:

    for each output slot, look up the position it should show;
    if a real frame already sits there, copy it, untouched;
    otherwise paint it from the two real frames either side.

The classes differ only in how wide a stretch gets corrected, and that is chosen
to match how far the defect actually reaches:

    PAD        the padded stretch. Positions land on whole numbers, so every
               real frame is copied bit for bit and only the wasted repeat slots
               are painted. Typically one frame in four is new.
    SEAM       a held frame and its doubled partner: ONE frame is painted, in
               the frozen slot, and the freeze is gone. Where a frame was simply
               dropped with nothing repeated in its place there is no slot to
               paint into, so the lurch is spread over four frames either side
               instead - a smaller repair, and it says so.
    PULLDOWN   the stretch the drops actually cover. `mountain 2` is a 30-into-24
               conversion for its first 178 frames and native 24 after that - the
               last 39 frames are never re-rendered.
    GRID       the whole region. Nothing is repeated and nothing is missing; the
               frames are simply in the wrong places, so they are moved back.

Options
  --dry-run     say what would happen, write nothing
  --crf 14      x264 quality, lower is bigger and better
  --gap flow|blend   force how invented frames are painted (default: measure
                both on this clip and keep whichever damages it less)
  --force       process even if the file looks already-repaired
  --keep-temp   leave the extracted frames behind (for debugging)
  --json        also write a machine-readable report beside the output

Set CADENCE_TMP to choose the scratch folder if your temp drive is small;
frame extraction needs roughly 3 MB per 1080p frame, 12 MB per 4K frame.
"""
import os, sys, glob, shutil, tempfile, subprocess, argparse, json, io, statistics, math
import hashlib, re
import concurrent.futures as cf

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import cadence_core as core
from cadence_core import RATE, Unreadable

print = core.print

CTX = 3             # real frames of context either side of a render chunk
CHUNK = 20          # pairs rendered per ffmpeg call
SNAP = 1 / 12.0     # a target this close to a real frame IS that frame

# How much of a clip each class is ever allowed to repaint.
#
# 3.1. A repair that repaints most of a clip is not a repair, it is a re-render,
# and the classes here are not re-render faults: a hitch about once a second
# touches a handful of frames and a padded beat touches one frame in three or
# four. When the count comes out far above that, the plan is wrong - and the one
# that produced this rule repainted 104 of 145 frames, left both held frames
# exactly where they were, and reported success. Stopping before writing the
# file is the whole point: a wrong repair that never reaches disk costs a minute,
# and one that does costs an afternoon of looking for it.
FOOTPRINT = {"SEAM": 0.25, "PAD": 0.60}


def pick_K(reqs):
    """How finely do we need to slice the gap between two frames?

    minterpolate computes EVERY intermediate frame at the rate you ask for, so
    asking for eighths costs four times what asking for halves costs. Most of
    the time we do not need eighths: a padded repeat wants the exact midpoint
    and nothing else, which is a half. So take the coarsest slicing that still
    lands every wanted position within a fiftieth of a frame - about two
    milliseconds, which nothing can see - and let the cheap cases be cheap.
    """
    alphas = [al for _, al in reqs.values()]
    for k in (2, 4, 8):
        if all(abs(al * k - round(al * k)) < 0.02 * k for al in alphas):
            return k
    return 8


# ------------------------------------------------------------------ helpers

DEBUG = os.environ.get("CADENCE_DEBUG")


def _run(cmd):
    r = subprocess.run(cmd, capture_output=True)
    if r.returncode != 0 and DEBUG:
        print("      ffmpeg failed:", " ".join(cmd[:12]), "...")
        print("      ", (r.stderr or b"").decode("utf-8", "replace").strip()[-400:])
    return r.returncode


def _place(src, dst):
    """Put a frame in the output sequence without spending disk on a second copy.

    A hard link is the same bytes under a second name, so assembling the output
    costs nothing. Copying instead doubles the scratch requirement at the very
    last step - which is how a job that measured its space, was told it had
    enough, and extracted every frame successfully, then died two seconds from
    the finish line with no room left. Falls back to a real copy on any
    filesystem that will not link.
    """
    try:
        if os.path.exists(dst):
            os.remove(dst)
        os.link(src, dst)
    except OSError:
        shutil.copy(src, dst)


def _blocks(buf, w, h, blk=80):
    out = []
    for by in range(0, h - blk + 1, blk):
        row = []
        for bx in range(0, w - blk, blk):
            t = 0
            for y in range(by, by + blk, 8):
                base = y * w
                t += sum(abs(buf[base + x + 1] - buf[base + x]) for x in range(bx, bx + blk))
            row.append(t / (blk * blk / 8))
        out.append(row)
    return out


TW, TH = 1280, 720


def _gray_png(p):
    return subprocess.run(["ffmpeg", "-v", "error", "-i", p, "-vf", f"scale={TW}:{TH}",
                           "-pix_fmt", "gray", "-f", "rawvideo", "-"],
                          capture_output=True).stdout


def _mean_detail(buf, step=3):
    tot = 0
    n = 0
    for y in range(2, TH - 2, step):
        row = y * TW
        for x in range(2, TW - 2, step):
            i = row + x
            tot += abs(4 * buf[i] - buf[i - 1] - buf[i + 1] - buf[i - TW] - buf[i + TW])
            n += 1
    return tot / n if n else 0.0


def _ghost(built, a, b, step=11):
    """corr(G-A, B-G) on one candidate frame. A 50/50 average scores exactly 1."""
    xs = [built[k] - a[k] for k in range(0, TW * TH, step)]
    ys = [b[k] - built[k] for k in range(0, TW * TH, step)]
    return core._corr(xs, ys)


def _detail_kept(built, a, b):
    """How much fine detail an invented frame keeps, scored on its WORST areas.

    Averages flatter optical flow: it holds most of the frame sharp and destroys
    a few regions outright, while a blend softens everything a little. On the
    average the flow wins; but it is the destroyed region the eye lands on. So
    this scores the 5th-percentile block, not the mean.
    """
    B, A, C = _blocks(built, TW, TH), _blocks(a, TW, TH), _blocks(b, TW, TH)
    r = []
    for i in range(len(B)):
        for j in range(len(B[0])):
            ref = (A[i][j] + C[i][j]) / 2
            if ref > 2.0:
                r.append(B[i][j] / ref)
    if not r:
        return 0.0
    r.sort()
    return r[max(0, int(len(r) * 0.05))]


def slowmo_factor(comp, v, given=None):
    """How many times slower the companion is. Refuses anything that is not clean."""
    c = core.probe(comp)
    if not c.nb_frames or not v.frames:
        return None, "could not read the slow-motion file"
    if given:
        F = int(given)
    else:
        F = int(round(c.nb_frames / v.frames))
    if F < 2:
        return None, (f"that file has {c.nb_frames} frames against the source's {v.frames} - "
                      f"it is not a slow-motion pass")
    off = abs(c.nb_frames - F * v.frames)
    if off > F:
        return None, (f"{c.nb_frames} frames is not {F}x {v.frames}; it is off by {off}. "
                      f"Re-render the slow-motion pass from THIS file, at a whole multiple, "
                      f"with no frames trimmed.")
    return F, None


def check_slowmo_alignment(comp, src, real, F, v, tmp, log=print):
    """Is the companion the same picture as the source, only slower?

    This matters more than it sounds. A picked frame lands BETWEEN two original
    frames, so it has to look like them. If the slow-motion pass was also
    upscaled, denoised, deblurred, regraded or grain-added, every picked frame
    will differ from its neighbours in exactly the way this whole exercise is
    trying to stop. Motion deblur especially: it is the right instinct applied
    at the wrong moment, because it makes the invented frames CRISPER than the
    real ones and the mismatch simply changes sign.

    Companion frame k*F should still BE source frame k. Check a few and say so.
    """
    d = os.path.join(tmp, "align")
    shutil.rmtree(d, ignore_errors=True); os.makedirs(d)
    ks = [k for k in real if k * F < (core.probe(comp).nb_frames or 0)]
    ks = ks[len(ks) // 4::max(1, len(ks) // 3)][:3]
    diffs = []
    for t, k in enumerate(ks):
        out = os.path.join(d, f"c{t}.png")
        if _run(["ffmpeg", "-v", "error", "-y", "-i", comp, "-vf",
                 f"select='eq(n\\,{k * F})',scale={TW}:{TH}", "-vsync", "0",
                 "-frames:v", "1", "-compression_level", "1", out]) != 0:
            continue
        a, b = _gray_png(src[k]), _gray_png(out)
        if not a or not b:
            continue
        n = len(a)
        diffs.append(sum(abs(a[i] - b[i]) for i in range(0, n, 7)) / (n / 7))
    shutil.rmtree(d, ignore_errors=True)
    if not diffs:
        return None
    return sum(diffs) / len(diffs)


def pick_from_slowmo(tmp, comp, real, reqs, v, F):
    """Fill each gap with a REAL frame out of the slow-motion pass.

    The plan already says, for every slot it has to fill, exactly where in source
    time that slot belongs. In a clean F-times slow-motion pass of the same file,
    that moment is not a fraction at all - it is a whole frame. So the tool stops
    inventing and starts picking, and every frame that reaches the timeline is one
    a renderer actually drew, with its own coherent motion blur.
    """
    out_dir = os.path.join(tmp, "picked")
    shutil.rmtree(out_dir, ignore_errors=True); os.makedirs(out_dir)
    wanted = {}
    for i, (j, alpha) in reqs.items():
        if j + 1 >= len(real):
            continue
        t = real[j] + alpha * (real[j + 1] - real[j])
        wanted[i] = int(round(t * F))
    if not wanted:
        return None, "nothing to pick"
    idxs = sorted(set(wanted.values()))
    expr = "+".join(f"eq(n\\,{k})" for k in idxs)
    rc = _run(["ffmpeg", "-v", "error", "-y", "-i", comp,
               "-vf", f"select='{expr}',scale={v.width}:{v.height}",
               "-vsync", "0", *RATE, "-compression_level", "6",
               os.path.join(out_dir, "p%05d.png")])
    got = sorted(glob.glob(os.path.join(out_dir, "p*.png")))
    if rc != 0 or len(got) != len(idxs):
        return None, (f"pulled {len(got)} of {len(idxs)} frames out of the slow-motion "
                      f"file - it may be shorter than its frame count claims")
    by_index = dict(zip(idxs, got))
    return {i: by_index[k] for i, k in wanted.items() if k in by_index}, None


# ------------------------------------------------------------ the renderer

def render_requests(tmp, src, real, pos, reqs, method, K=8, workers=3):
    """reqs: {output_slot: (j, alpha)} where j indexes `real`, 0 < alpha < 1.

    Returns {output_slot: png path}. Frames are painted in chunks: neighbouring
    gaps share one ffmpeg call, so a clip where every frame needs painting costs
    a handful of passes rather than one pass per frame.

    Anything already painted on an earlier, interrupted run is picked up as-is,
    so a 4K job that was stopped can simply be started again.
    """
    made = {}
    if not reqs:
        return made
    done = {s: os.path.join(tmp, f"mid_{s:05d}.png") for s in reqs}
    made = {s: p for s, p in done.items() if os.path.exists(p)}
    if made:
        print(f"    resuming: {len(made)} frames were already painted")
    reqs = {s: v for s, v in reqs.items() if s not in made}
    if not reqs:
        return made

    # group the needed pairs into runs of neighbours, then into chunks
    pairs = sorted({j for j, _ in reqs.values()})
    runs, cur = [], [pairs[0]]
    for x, y in zip(pairs, pairs[1:]):
        if y - x <= 2:
            cur.append(y)
        else:
            runs.append(cur); cur = [y]
    runs.append(cur)
    chunks = []
    for run in runs:
        for s in range(0, len(run), CHUNK):
            chunks.append(run[s:s + CHUNK])

    by_pair = {}
    for slot, (j, al) in reqs.items():
        by_pair.setdefault(j, []).append((slot, al))

    def do_chunk(ch):
        lo = max(0, ch[0] - CTX)
        hi = min(len(real) - 1, ch[-1] + 1 + CTX)
        w = os.path.join(tmp, f"w{ch[0]:05d}")
        shutil.rmtree(w, ignore_errors=True); os.makedirs(w)
        for n, u in enumerate(range(lo, hi + 1)):
            shutil.copy(src[real[u]], os.path.join(w, f"w{n + 1:03d}.png"))

        # Several output slots can land on the same sub-frame position once the
        # alphas are rounded to 1/K. Ask ffmpeg for each position ONCE and hand
        # the picture to every slot that wanted it - asking twice makes select
        # emit one frame for two requests, and then the whole chunk fails the
        # count check and gets thrown away.
        want = {}                                  # intermediate index -> [slots]
        for j in ch:
            for slot, al in by_pair[j]:
                s = min(K - 1, max(1, int(round(al * K))))
                want.setdefault((j - lo) * K + s, []).append(slot)
        order = sorted(want)
        got = {}

        if method == "blend":
            for idx in order:
                j, al = idx // K, (idx % K) / K
                dst = os.path.join(tmp, f"mid_{order.index(idx):05d}_{idx}.png")
                rc = _run(["ffmpeg", "-v", "error", "-y",
                           "-i", os.path.join(w, f"w{j + 1:03d}.png"),
                           "-i", os.path.join(w, f"w{j + 2:03d}.png"),
                           "-filter_complex", f"[0][1]blend=all_expr='A*(1-{al})+B*{al}'",
                           "-compression_level", "1", dst])
                if rc == 0 and os.path.exists(dst):
                    for slot in want[idx]:
                        p = os.path.join(tmp, f"mid_{slot:05d}.png")
                        shutil.copy(dst, p); got[slot] = p
                    os.remove(dst)
            shutil.rmtree(w, ignore_errors=True)
            return got

        sel = "+".join(f"eq(n\\,{i})" for i in order)
        rc = _run(["ffmpeg", "-v", "error", "-y", "-framerate", "24",
                   "-i", os.path.join(w, "w%03d.png"), "-vf",
                   f"minterpolate=fps={24 * K}:mi_mode=mci:mc_mode=aobmc:"
                   f"me_mode=bidir:vsbmc=1,select='{sel}'",
                   *RATE, "-compression_level", "1", os.path.join(w, "m%04d.png")])
        outs = sorted(glob.glob(os.path.join(w, "m*.png")))
        if rc == 0 and len(outs) == len(order):
            for idx, f in zip(order, outs):
                for slot in want[idx]:
                    dst = os.path.join(tmp, f"mid_{slot:05d}.png")
                    shutil.copy(f, dst); got[slot] = dst
        shutil.rmtree(w, ignore_errors=True)
        return got

    if workers > 1:
        with cf.ThreadPoolExecutor(max_workers=workers) as ex:
            for got in ex.map(do_chunk, chunks):
                made.update(got)
    else:
        for ch in chunks:
            made.update(do_chunk(ch))

    # Anything a chunk could not deliver is retried one pair at a time, alone,
    # with all the memory. A whole chunk failing over a single awkward gap is
    # not a reason to leave twenty frames unpainted.
    stragglers = sorted({j for slot, (j, _) in reqs.items() if slot not in made})
    for j in stragglers:
        made.update(do_chunk([j]))

    # Last resort: a plain cross-fade at the right fraction. Never leave a slot
    # unpainted - an unpainted slot falls back to a copy of its neighbour, which
    # is the exact stutter this tool was run to remove, sitting in the output
    # while the log says the repair succeeded.
    left = {s: v for s, v in reqs.items() if s not in made}
    if left:
        if DEBUG:
            print(f"      {len(left)} slot(s) fell back to a cross-fade")
        for slot, (j, al) in left.items():
            if j + 1 >= len(real):
                continue
            dst = os.path.join(tmp, f"mid_{slot:05d}.png")
            if _run(["ffmpeg", "-v", "error", "-y", "-i", src[real[j]], "-i", src[real[j + 1]],
                     "-filter_complex", f"[0][1]blend=all_expr='A*(1-{al:.4f})+B*{al:.4f}'",
                     "-compression_level", "1", dst]) == 0 and os.path.exists(dst):
                made[slot] = dst
    return made


def choose_method(tmp, src, real, reqs, forced=None):
    """Try both ways of painting a frame ON THIS CLIP and keep the better.

    Optical flow beats a blend on gentle motion and loses badly on fast action,
    where the displacement outruns what block matching can follow and the
    invented frame dissolves. Which one wins depends on the footage, so it is
    measured rather than assumed - and it is measured HERE, once, so the
    diagnosis and the repair cannot end up disagreeing about it.
    """
    if forced:
        return forced, None
    js = sorted({j for j, _ in reqs.values()})
    if not js:
        return "flow", None
    picks = js[len(js) // 6::max(1, len(js) // 3)][:3] or js[:1]
    fl = bl = 0.0
    fg = bg = rg = 0.0            # ghost: flow, blend, and this clip's own real frames
    fm = bm = 0.0                 # blur match against the neighbours, 1.0 = same
    n = 0
    d = os.path.join(tmp, "probe")
    for j in picks:
        if j + 1 >= len(real):
            continue
        shutil.rmtree(d, ignore_errors=True); os.makedirs(d)
        lo, hi = max(0, j - 2), min(len(real) - 1, j + 3)
        for t, u in enumerate(range(lo, hi + 1)):
            _run(["ffmpeg", "-v", "error", "-y", "-i", src[real[u]], "-vf",
                  f"scale={TW}:{TH}", "-compression_level", "1",
                  os.path.join(d, f"w{t + 1:03d}.png")])
        a = os.path.join(d, f"w{j - lo + 1:03d}.png")
        b = os.path.join(d, f"w{j - lo + 2:03d}.png")
        if not (os.path.exists(a) and os.path.exists(b)):
            continue
        ok = _run(["ffmpeg", "-v", "error", "-y", "-framerate", "24",
                   "-i", os.path.join(d, "w%03d.png"), "-vf",
                   "minterpolate=fps=48:mi_mode=mci:mc_mode=aobmc:me_mode=bidir:vsbmc=1",
                   *RATE, "-compression_level", "1", os.path.join(d, "m%03d.png")])
        m = sorted(glob.glob(os.path.join(d, "m*.png")))
        idx = 2 * (j - lo) + 1
        if ok != 0 or not (0 <= idx < len(m)):
            continue
        _run(["ffmpeg", "-v", "error", "-y", "-i", a, "-i", b, "-filter_complex",
              "[0][1]blend=all_expr='(A+B)/2'", "-compression_level", "1",
              os.path.join(d, "blend.png")])
        ga, gb = _gray_png(a), _gray_png(b)
        if not ga or not gb:
            continue
        gf = _gray_png(m[idx])
        gb_img = _gray_png(os.path.join(d, "blend.png"))
        fl += _detail_kept(gf, ga, gb)
        bl += _detail_kept(gb_img, ga, gb)

        # How much like a double image is each candidate - measured against a
        # real triple out of this same clip, so the bar is set by the footage
        # rather than by a constant picked in advance.
        ef, eb = _ghost(gf, ga, gb), _ghost(gb_img, ga, gb)
        rc = None
        third = os.path.join(d, f"w{j - lo + 3:03d}.png")
        if os.path.exists(third):
            gc = _gray_png(third)
            if gc:
                rc = _ghost(gb, ga, gc)   # a real triple: b against a and c
        fg += ef if ef is not None else 0.0
        bg += eb if eb is not None else 0.0
        rg += rc if rc is not None else 0.0

        # And how close each sits to the neighbours in fine detail - the other
        # half of "an invented frame should look like the ones either side".
        ref = (_mean_detail(ga) + _mean_detail(gb)) / 2 or 1e-9
        fm += _mean_detail(gf) / ref
        bm += _mean_detail(gb_img) / ref
        n += 1
    shutil.rmtree(d, ignore_errors=True)
    if not n:
        return "flow", None
    fl /= n; bl /= n; fg /= n; bg /= n; rg /= n; fm /= n; bm /= n

    # The old rule here was "keep whichever holds more fine detail", and it was
    # wrong in a way that only showed on fast action. A blend of two sharp frames
    # keeps every edge - twice - so it SCORES WELL on detail exactly when it is
    # at its worst, and the chooser reached for a double image precisely when the
    # motion was fastest. Measured on a combat clip: the blend's invented frames
    # came back at 0.997 against real frames at -0.18. Twenty perfect ghosts.
    #
    # So detail is no longer the criterion. The criterion is resemblance to the
    # neighbours, in both of the ways that can go wrong:
    #
    #   ghosting  - how much more double-imaged than this clip's own real frames
    #   blur      - how far the fine detail sits from the frames either side
    #
    # Ghosting is weighted far harder because it is far more visible: a slightly
    # soft frame reads as softness, a doubled frame reads as a second stutter.
    def cost(ghost, match):
        return 3.0 * max(0.0, ghost - rg) + abs(math.log(max(match, 1e-3)))

    cf, cb = cost(fg, fm), cost(bg, bm)
    pick = "flow" if cf <= cb else "blend"
    return pick, (fl, bl, fg, bg, rg, fm, bm, cf, cb)


# ------------------------------------------------------------------- report

CLASS_TEXT = core.CLASS_TEXT
describe = core.describe


# --------------------------------------------------------------------- main

def main(path, a):
    try:
        v = core.diagnose(path, force=a.force, assume_fps=a.fps,
                          force_class=a.force_class)
    except Unreadable as e:
        print(f"{os.path.basename(path)}: SKIPPED - {e}")
        return True
    describe(v)

    if not v.ok:
        core.log_run(v, "fix", outcome="refused")
        return True

    if v.klass in ("CLEAN", "STATIC", "IRREGULAR", "AMBIGUOUS"):
        core.log_run(v, "fix", repainted=0, outcome="left alone")
        print()
        if v.klass == "AMBIGUOUS":
            print("  Not repaired. Two explanations fit this clip about equally well, and")
            print("  guessing between them is how the wrong repair gets applied. The")
            print("  evidence is above. If you know which it is, re-run with")
            print("  --force-class PAD | SEAM | PULLDOWN | GRID.")
        else:
            print("  Nothing to repair. This clip is left exactly as it is.")
        return True

    pl = core.plan(v, seam_window=a.seam_window)
    pos, target, real = pl["pos"], pl["target"], pl["real"]
    n = v.frames

    # work out, per output slot, whether it is a copy or has to be painted
    reqs, copies = {}, {}
    rp = [pos[i] for i in real]
    cursor = 0
    for i in range(n):
        p = target[i]
        while cursor + 1 < len(rp) and rp[cursor + 1] <= p + 1e-9:
            cursor += 1
        j = cursor
        # Close enough to a real frame IS a real frame.
        #
        # 3.1, and it only started to matter with GRID: on an uneven grid the
        # corrections are fractions of a frame, and some of them come out at a
        # fiftieth. Painting a frame to move it that far spends a real picture
        # to buy a change nothing can see - and an invented frame is always a
        # little softer than the one it replaced. Anything inside a twelfth of
        # a frame, about four milliseconds, is left as the real picture it is.
        near = j
        if j + 1 < len(rp) and abs(rp[j + 1] - p) < abs(rp[j] - p):
            near = j + 1
        if abs(p - rp[near]) < SNAP:
            copies[i] = real[near]
        elif j + 1 < len(rp) and rp[j + 1] > rp[j]:
            copies[i] = None
            reqs[i] = (j, (p - rp[j]) / (rp[j + 1] - rp[j]))
        else:
            copies[i] = real[j]

    # A lost frame with no real frame after it cannot be painted - there is
    # nothing on the far side to move towards. That is one frame at the very end
    # of the clip and it stays as the copy it already is. Saying so is better
    # than reporting it later as a mysterious failure.
    edge = [i for i, (j, _) in reqs.items() if j + 1 >= len(rp)]
    for i in edge:
        reqs.pop(i)
        copies[i] = real[min(len(real) - 1, cursor)]

    print()
    core.plan_summary(v, pl, len(reqs), fps=a.fps, edge=edge)

    cap = FOOTPRINT.get(v.klass)
    if cap and len(reqs) > cap * n and not a.force:
        print()
        print(f"  STOPPING - the plan repaints {len(reqs)} of {n} frames "
              f"({len(reqs) / n:.0%}).")
        for ln in core.wrap(
                f"A {v.klass} fault does not touch that much of a clip. This one is "
                f"allowed {cap:.0%} and no more, because a plan this large means the "
                f"diagnosis or the plan is wrong, and a file written from it looks "
                f"exactly like a good one on disk. Nothing has been written. Run "
                f"2-DIAGNOSE-ONLY on this clip and read the evidence, or re-run with "
                f"--force if you know better.", 70):
            print("  " + ln)
        core.log_run(v, "fix", repainted=len(reqs), outcome="refused - footprint too large")
        return False

    if a.dry_run:
        return True
    if not reqs:
        print("    nothing actually needs repainting; leaving the file alone")
        return True

    # ---- extract ---------------------------------------------------------
    # One scratch folder PER INPUT FILE, not one shared by everything.
    #
    # 3.1. The default used to be a single %TEMP%\cadencefix, which is fine
    # until you do the obvious thing and run three queues at once to get a batch
    # done - and then run two destroys the frames run one is halfway through
    # reading, and both produce nonsense with no error anywhere. The name is
    # derived from the input path, so re-running the SAME clip still finds the
    # frames it extracted last time and skips the extraction, which is the one
    # thing the shared folder was good for.
    key = hashlib.sha1(os.path.abspath(path).encode("utf-8", "replace")).hexdigest()[:10]
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", os.path.splitext(os.path.basename(path))[0])[:40]
    tmp = os.path.join(os.environ.get("CADENCE_TMP", tempfile.gettempdir()),
                       "cadencefix", f"{safe}-{key}")
    stamp = f"{os.path.abspath(path)}|{os.path.getmtime(path)}|{n}|{core.VERSION}"
    try:
        same = io.open(os.path.join(tmp, "stamp.txt")).read() == stamp
    except Exception:
        same = False
    if not same:
        shutil.rmtree(tmp, ignore_errors=True)
        os.makedirs(os.path.join(tmp, "src"))
        io.open(os.path.join(tmp, "stamp.txt"), "w").write(stamp)
    os.makedirs(os.path.join(tmp, "src"), exist_ok=True)
    os.makedirs(os.path.join(tmp, "out"), exist_ok=True)
    if not glob.glob(os.path.join(tmp, "src", "*.png")):
        # Measure three real frames before committing to all of them, and check
        # there is room. Running out of disk halfway through leaves a folder of
        # part-extracted frames and no explanation, which is a miserable way to
        # find out.
        probe_dir = os.path.join(tmp, "sizecheck")
        shutil.rmtree(probe_dir, ignore_errors=True); os.makedirs(probe_dir)
        _run(["ffmpeg", "-v", "error", "-y", "-i", path, "-frames:v", "3",
              *RATE, "-compression_level", "6",
              os.path.join(probe_dir, "s%02d.png")])
        got = glob.glob(os.path.join(probe_dir, "*.png"))
        per = (sum(os.path.getsize(f) for f in got) / len(got)) if got else 3e6
        shutil.rmtree(probe_dir, ignore_errors=True)
        need = per * (n + len(reqs)) * 1.15
        free = shutil.disk_usage(tmp).free
        print(f"    extracting {n} frames (about {need / 1e6:.0f} MB of scratch space, "
              f"{free / 1e6:.0f} MB free)")
        if free < need:
            print()
            print(f"    NOT ENOUGH ROOM. This clip needs about {need / 1e6:.0f} MB of")
            print(f"    scratch space and there is {free / 1e6:.0f} MB free where the")
            print(f"    temporary files go. Point CADENCE_TMP at a drive with room:")
            print(f"        set CADENCE_TMP=D:\\cadence_scratch")
            print(f"    then run this again. Nothing has been changed.")
            return False
        if _run(["ffmpeg", "-v", "error", "-i", path, "-start_number", "0",
                 *RATE, "-compression_level", "6",
                 os.path.join(tmp, "src", "%05d.png")]) != 0:
            print("    FAILED to extract frames - out of disk space? Set CADENCE_TMP.")
            return False
    src = sorted(glob.glob(os.path.join(tmp, "src", "*.png")))
    if len(src) < n:
        print(f"    only {len(src)} of {n} frames extracted - aborting rather than "
              f"writing a short file")
        return False

    # ---- fill the gaps ---------------------------------------------------
    topaz = False
    if a.from_slowmo:
        F, why = slowmo_factor(a.from_slowmo, v, a.slowmo_factor)
        if not F:
            print(f"    cannot use that slow-motion file: {why}")
            return False
        print(f"    picking real frames out of {os.path.basename(a.from_slowmo)} "
              f"({F}x slower)")
        drift = check_slowmo_alignment(a.from_slowmo, src, real, F, v, tmp)
        if drift is None:
            print("      could not confirm it lines up with the source - carrying on, "
                  "check the result by eye")
        elif drift > 6.0:
            print(f"      IT DOES NOT MATCH THE SOURCE (frames differ by {drift:.1f} of 255).")
            print( "      Frame k*F in that file should still BE frame k of the source. It")
            print( "      is not, so the pass carried an enhancement as well as the slowdown")
            print( "      - an upscale, a denoise, a deblur, added grain, a regrade. Picked")
            print( "      frames would sit beside the originals and not match them, which is")
            print( "      the exact fault this is meant to remove.")
            print( "      Re-render the slow-motion on its own, with everything else off.")
            return False
        else:
            print(f"      lines up with the source (frames differ by {drift:.1f} of 255)")
        made, why = pick_from_slowmo(tmp, a.from_slowmo, real, reqs, v, F)
        if not made:
            print(f"    {why}")
            return False
        method, scores = f"picked from a {F}x pass", None
        print(f"    picked {len(made)} real frames - nothing was invented")
    else:
        method, scores = choose_method(tmp, src, real, reqs, a.gap)
        if scores and len(scores) >= 9:
            fl, bl, fg, bg, rg, fm, bm, cf, cb = scores
            print( "    invented-frame test on this clip:")
            print(f"      detail kept    flow {fl * 100:.0f}%    blend {bl * 100:.0f}%")
            print(f"      double image   flow {fg:+.2f}    blend {bg:+.2f}"
                  f"    (real frames here: {rg:+.2f})")
            print(f"      blur match     flow {fm:.2f}     blend {bm:.2f}"
                  f"     (1.00 = same as the neighbours)")
            print(f"      -> {method}   (cost {min(cf, cb):.2f} against {max(cf, cb):.2f})")
        elif scores:
            print(f"    invented-frame test: flow {scores[0] * 100:.0f}%, "
                  f"blend {scores[1] * 100:.0f}%")
    if not a.from_slowmo:
        made = None
    Kq = pick_K(reqs)
    px = (v.width or 1920) * (v.height or 1080)
    workers = 1 if px >= 3800 * 2100 else (2 if px >= 1900 * 1000 else 3)
    if made is None:
        print(f"    painting {len(reqs)} frames by {method}"
              + (f", in {'halves' if Kq == 2 else 'quarters' if Kq == 4 else 'eighths'} "
                 f"of a frame" if method == "flow" else ""))
        if workers == 1:
            print("      4K or larger - one stretch at a time, to stay inside memory")
        if method == "blend":
            print("      the motion here is gentle enough that averaging the two")
            print("      neighbours is invisible. On faster motion it would not be, and")
            print("      the chooser would not have picked it.")
        topaz = bool(scores and max(scores[0], scores[1]) < 0.55)

    if made is None:
        made = render_requests(tmp, src, real, pos, reqs, method, K=Kq, workers=workers)
    if len(made) < len(reqs):
        print(f"    only {len(made)} of {len(reqs)} frames could be painted "
              f"({len(reqs) - len(made)} failed - ffmpeg out of memory?)")
    if not made:
        print("    nothing could be painted - aborting, file left alone")
        return False

    # ---- assemble --------------------------------------------------------
    for f in glob.glob(os.path.join(tmp, "out", "*.png")):
        os.remove(f)
    written = 0
    for i in range(n):
        s = made.get(i) or (src[copies[i]] if copies.get(i) is not None else None)
        if s is None:
            s = src[min(real, key=lambda u: abs(pos[u] - target[i]))]
        _place(s, os.path.join(tmp, "out", f"{written:05d}.png"))
        written += 1
    if written != n:
        print(f"    assembled {written} frames but the source has {n} - aborting")
        return False

    stem, ext = os.path.splitext(path)
    # Never write over a repair that is already there. An earlier attempt is the
    # only thing a new one can be compared against, and a tool that destroys it
    # to save a filename is asking to be trusted with no way to check it.
    out = core.free_even_output(path)
    outstem = out[:-len(ext)] if ext else out
    if out != f"{stem}_even{ext}":
        print(f"    an earlier repair is already there - writing {os.path.basename(out)}")
    cmd = ["ffmpeg", "-v", "error", "-y", "-framerate", str(a.fps),
           "-i", os.path.join(tmp, "out", "%05d.png")]
    if v.has_audio:
        cmd += ["-i", path, "-map", "0:v", "-map", "1:a", "-c:a", "copy"]
    cmd += ["-c:v", "libx264", "-crf", str(a.crf), "-preset", "slow",
            "-pix_fmt", "yuv420p", out]
    if _run(cmd) != 0:
        print("    FAILED during encoding")
        return False

    # ---- verify ----------------------------------------------------------
    print()
    print("  CHECK (measured on the file that was just written)")
    before = core.evenness(path)
    after = core.evenness(out)
    ok = True
    try:
        c2 = core.probe(out)
        good_len = (c2.nb_frames in (None, n)) and abs((c2.duration or 0) - v.duration) < 0.02
        print(f"    frames {v.frames} -> {c2.nb_frames or n}, "
              f"{v.duration:.3f}s -> {(c2.duration or v.duration):.3f}s, "
              f"{v.fps:g} fps -> {c2.fps:g} fps"
              + ("   OK" if good_len else "   ** LENGTH CHANGED **"))
        ok = ok and good_len
    except Exception:
        pass
    sb = sa = None
    if before is not None and after is not None:
        b0, b1 = before
        a0, a1 = after
        # Two different shapes of fault need two numbers. `spread` catches a
        # fault in every frame - padding, a rate conversion. `worst` catches a
        # fault in three frames out of ninety, which barely moves a percentile
        # but is the thing you actually see. One lone 1.8x hitch is more visible
        # than a slight wobble everywhere, so worst carries real weight here.
        sb, sa = b0 + 0.5 * (b1 - 1), a0 + 0.5 * (a1 - 1)
        print(f"    unevenness {b0:.2f} -> {a0:.2f}   worst hitch "
              f"{b1:.2f}x -> {a1:.2f}x   (a clip with nothing wrong reads about "
              f"0.05 and 1.1x)")

    # The strongest check there is: run the diagnosis again on the file that was
    # just written. If the engine still recognises the defect, the repair did
    # not work, whatever the numbers say.
    #
    # IRREGULAR used to count as a clean bill of health here. It must not. It is
    # the engine saying "there are big steps in this clip and I have decided they
    # are motion" - and a half-finished repair, where the freeze was removed and
    # the lurch left behind, looks exactly like that from the inside. Treating it
    # as proof of success is how a clip that still stutters gets handed back with
    # an encouraging word on it.
    gone = False
    irregular = False
    checked = False
    try:
        v2 = core.diagnose(out, force=True, assume_fps=a.fps)
        checked = True
        gone = v2.klass in ("CLEAN", "STATIC")
        irregular = v2.klass == "IRREGULAR"
        if gone:
            note = "   (the defect is gone)"
        elif irregular:
            note = ("   (no longer the original defect, but big steps it is calling "
                    "motion - not proof it is fixed)")
        else:
            note = "   ** still reads as broken **"
        print(f"    re-diagnosed as: {v2.klass}" + note)
    except Exception:
        pass

    # ---- texture ---------------------------------------------------------
    # Everything above measures motion. This measures the picture, because a
    # repair can put the movement in exactly the right place and still leave the
    # frames it painted softer than the ones it copied - and on a padded clip
    # those land every fourth frame, so the difference arrives on a beat.
    tex = None
    try:
        tex = core.texture(out, sorted(made.keys()))
    except Exception:
        pass
    if tex:
        ratio, n_paint, n_plain = tex
        if ratio >= 0.95:
            print(f"    texture: repainted frames match the ones beside them "
                  f"({ratio:.2f}x) - nothing more to do")
        else:
            print(f"    texture: repainted frames carry {(1 - ratio) * 100:.0f}% less fine "
                  f"detail than the frames beside them ({ratio:.2f}x)")
            print( "      The motion is right - this is the picture. An invented frame is")
            print( "      built out of two real ones and averaging costs fine detail, so the")
            print( "      repaints are softer than their neighbours. They land on a regular")
            print( "      beat, which reads as a pulse rather than as softness.")
            print( "      Fix it with a pass over EVERY frame afterwards, so the whole clip")
            print( "      gets the same treatment and nothing stands out: Topaz ganim, or any")
            print( "      denoise or sharpen applied to the clip as a whole. Do it in that")
            print( "      order - repair first, enhance second.")

    gh = None
    try:
        gh = core.ghosting(out, sorted(made.keys()))
    except Exception:
        pass
    ghost_bad = False
    if gh:
        g_paint, g_real, _n = gh
        excess = g_paint - g_real
        ghost_bad = excess > 0.5
        if ghost_bad:
            print(f"    ** DOUBLE IMAGES ** the repainted frames read {g_paint:+.2f} against "
                  f"{g_real:+.2f} for the real frames in this clip")
            print( "      They are averages of their neighbours, not frames in their own")
            print( "      right - two pictures on top of each other. That is not blur and")
            print( "      no grade will hide it. Re-run with --gap flow, or better, give it")
            print( "      a slow-motion pass to pick real frames out of: --from-slowmo FILE")
        else:
            print(f"    double image: none - repainted frames read {g_paint:+.2f} against "
                  f"{g_real:+.2f} for the real ones")

    word = "done"
    if sa is not None:
        better = sa < sb * 0.9
        worse = sa > sb * 1.1
        if ghost_bad:
            # Nothing else matters if the invented frames are double images. The
            # motion can be perfectly even and the clip still unusable.
            word = "** DOUBLE IMAGES - do not use this file, keep the original **"
        elif worse:
            # The measurement outranks the re-diagnosis, always. A repair that
            # leaves the clip measurably less even than it found it has failed,
            # whether or not the engine still recognises the original defect -
            # and "the defect is gone" is exactly what a half-finished repair
            # reports, because what is left no longer matches the pattern it
            # started as.
            word = "** NO BETTER - do not use this file, keep the original **"
        elif gone and better:
            word = "evened out" if sa < sb * 0.5 else "improved"
        elif gone:
            word = "defect gone, but barely more even - check it by eye"
        elif better:
            word = ("more even, but the engine still sees the defect"
                    if checked and not irregular else
                    "more even, but not conclusively clean") + " - check it by eye"
        else:
            word = "** NO BETTER - do not use this file, keep the original **"
        print(f"    overall {sb:.2f} -> {sa:.2f}   {word}")
        ok = ok and better and not worse and not ghost_bad

    # ---- a condemned file does not get a good file's name -------------------
    #
    # 3.1, and this one is not clever, it is just overdue. The checks above work:
    # they caught genuine double images in four clips out of sixty and printed
    # DO NOT USE THIS FILE in capitals. And then the file was written as
    # <name>_even.mp4 anyway - the same name a good repair gets - so on disk, a
    # week later, there is nothing whatsoever to tell them apart. The warning
    # lived in a console window that is long closed.
    #
    # So the verdict goes in the filename, where it survives. A condemned file is
    # kept rather than deleted, because it is evidence of what went wrong and
    # sometimes the only copy of a nearly-right result - but nothing is ever
    # going to mistake it for a repair.
    condemned = bool(sa is not None and not ok)
    if condemned:
        stem2, ext2 = os.path.splitext(out)
        bad = f"{stem2}_REJECTED{ext2}"
        k = 2
        while os.path.exists(bad):
            bad = f"{stem2}_REJECTED_{k}{ext2}"
            k += 1
        try:
            os.replace(out, bad)
            out = bad
            outstem = bad[:-len(ext2)] if ext2 else bad
            print()
            print(f"    written as {os.path.basename(out)} - the name carries the verdict,")
            print( "    so it cannot be mistaken for a good repair later. Keep the original.")
        except OSError as e:
            print(f"    (could not rename the condemned file: {e})")

    logged = core.log_run(v, "fix", repainted=len(reqs), method=method,
                          before=before, after=after, outcome=word, texture=tex)

    if topaz:
        print()
        print("    NOTE: neither optical flow nor a blend paints a clean frame on this")
        print("    clip - the motion between frames is past what ffmpeg can follow.")
        print("    The file above is the best free result. If it looks mushy on the")
        print("    repainted frames, Topaz Apollo (apo-8, 'replace duplicate frames')")
        print("    on the RAW download is the one thing that does better.")

    print()
    print("  done:" if not condemned else "  NOT USABLE:", os.path.basename(out))
    if logged:
        print(f"  logged to {os.path.basename(logged)}")

    rep = (f"source   : {v.name}\n"
           f"tool     : cadence_fix {core.VERSION}\n"
           f"diagnosis: {v.klass} - {CLASS_TEXT.get(v.klass, '')}\n"
           f"repair   : {[ (s[0], s[1], s[2]) for s in pl['spans'] ]}\n"
           f"repainted: {len(reqs)} of {n} frames, by {method}\n"
           f"output   : {n} frames, {a.fps:g} fps, {n / a.fps:.3f}s - "
           f"same length as the source, drop straight on a {a.fps:g} fps timeline\n"
           f"unevenness before/after: {before} -> {after}\n"
           + (f"doubled   : repainted {gh[0]:+.2f} vs real {gh[1]:+.2f}"
              + ("  ** DOUBLE IMAGES - DO NOT USE **\n" if ghost_bad else "  (fine)\n")
              if gh else "")
           + (f"texture   : repainted frames read {tex[0]:.2f}x the detail of the frames "
              f"beside them"
              + ("\n" if tex[0] >= 0.95 else
                 " - run a pass over EVERY frame (Topaz ganim, or a\n"
                 "            whole-clip denoise/sharpen) to even the texture out\n")
              if tex else ""))
    io.open(f"{outstem}_REPORT.txt", "w", encoding="utf-8").write(rep)

    if a.json:
        io.open(f"{outstem}.json", "w", encoding="utf-8").write(
            json.dumps({"verdict": v.as_dict(), "spans": pl["spans"],
                        "repainted": len(reqs), "method": method,
                        "evenness_before": before, "evenness_after": after},
                       indent=2, default=str))
    if not a.keep_temp:
        shutil.rmtree(tmp, ignore_errors=True)
    return not condemned


ap = argparse.ArgumentParser()
ap.add_argument("inputs", nargs="+")
ap.add_argument("--fps", type=float, default=24.0)
ap.add_argument("--crf", type=int, default=14)
ap.add_argument("--dry-run", action="store_true")
ap.add_argument("--gap", choices=["flow", "blend"], default=None)
ap.add_argument("--seam-window", type=int, default=4,
                help="how far either side of a dropped frame the lurch is spread, "
                     "where nothing was repeated. Held frames ignore this - they "
                     "are repainted in place, one frame each.")
ap.add_argument("--force", action="store_true")
ap.add_argument("--from-slowmo", default=None, metavar="FILE",
                help="fill the gaps with real frames out of this slow-motion pass of "
                     "the same clip, instead of inventing them")
ap.add_argument("--slowmo-factor", type=int, default=None,
                help="how many times slower that file is (default: work it out)")
ap.add_argument("--force-class", choices=["PAD", "SEAM", "PULLDOWN", "GRID"], default=None,
                help="overrule the diagnosis and repair as if it were this class")
ap.add_argument("--keep-temp", action="store_true")
ap.add_argument("--json", action="store_true")
a = ap.parse_args()

# 3.1: the exit code now means something.
#
# It used to be 0 whatever happened - after a crash, after FAILED during
# encoding, after a file was condemned in capital letters. The launcher checks
# it and so a ten-clip batch with two crashes in it still finished with
# "ALL DONE - Problems: 0" and told you the repairs were saved. A tool that
# reports its own failures as successes is worse than one that fails.
failures = 0

for p in a.inputs:
    print()
    print("=" * 70)
    try:
        if main(p, a) is False:
            failures += 1
    except OSError as e:
        if getattr(e, "errno", None) == 28:
            print(f"{os.path.basename(p)}: RAN OUT OF DISK while working.")
            print( "  Nothing was written and your original is untouched. Point the")
            print( "  scratch folder at a drive with more room and run it again:")
            print( "      set CADENCE_TMP=D:\\cadence_scratch")
            print( "  (then start the icon from that same command window)")
        else:
            print(f"{os.path.basename(p)}: FAILED - {type(e).__name__}: {e}")
        failures += 1
    except Exception as e:
        print(f"{os.path.basename(p)}: FAILED - {type(e).__name__}: {e}")
        failures += 1

sys.exit(1 if failures else 0)
