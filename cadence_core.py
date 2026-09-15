#!/usr/bin/env python3
"""
cadence_core.py - the ONE engine that measures a clip and decides what is wrong.

Nothing else in this folder is allowed to have its own opinion. Both the
DIAGNOSE button and the FIX button import this file and read the same verdict,
so they can never disagree about a clip. That disagreement is what produced the
"right tool, wrong problem" results: the checker used one rule and the repairer
used another, and whichever you believed, the other one was doing something else.

The order of work matters and is deliberate:

    1  READ THE CONTAINER   frame rate, duration, audio, encoder tags.
                            A clip that is not 24 fps, or whose timestamps are
                            uneven, has a container problem. No amount of
                            pixel repair fixes that, so it is caught first.

    2  MEASURE THE PIXELS   one decode pass. Per step we keep three numbers,
                            not one: how much changed, how much the BUSIEST
                            small block changed, and how much the overall
                            brightness changed. One number cannot tell a
                            repeated frame from a slow pan, or a cut from a
                            missing frame. Three can.

    3  CUT THE CLIP UP      hard cuts, a held opening pose, a held ending, and
                            any long freeze in the middle are found and set
                            aside BEFORE any arithmetic. This is the step that
                            was missing. A clip with 18 static frames at the
                            head had those frames dragged into the frame-rate
                            sum, which threw the answer off and then the wrong
                            repair was applied to the whole clip.

    4  CLASSIFY EACH REGION separately, against evidence, with a margin. A clip
                            is often broken in one stretch and perfectly fine in
                            another; treating it as one thing is how a clean
                            passage ends up being re-rendered for no reason.

    5  PLAN THE REPAIR      as target positions on a motion timeline. Every
                            class produces the same shape of answer, so there is
                            exactly one renderer and one place to get it wrong.

Classes, and the repair each one earns:

    CLEAN       motion is already even                     do nothing
    STATIC      a held pose - the scene really did stop     do nothing
    PAD         every Nth frame is a repeat of the one      repaint the wasted
                before it; the real frame was lost          slot in place
    SEAM        a handful of isolated missing frames        even out a short
                                                            window around each
    PULLDOWN    frames dropped throughout on a repeating    even out the whole
                cycle (a 25 or 30 fps clip forced to 24)    region
    GRID        no frame repeated anywhere, but the steps   redistribute the
                alternate long/short on a strict beat       whole region
    IRREGULAR   big steps, but they are bursts of fast      do nothing
                motion, not a defect
    AMBIGUOUS   two explanations fit about equally well     do nothing, say so

Needs only python3 and ffmpeg. numpy is used if present and is not required.
"""
from __future__ import annotations
import subprocess, statistics, os, sys, json, math, functools

print = functools.partial(print, flush=True)

try:
    import numpy as _np
except Exception:
    _np = None

# Analysis resolution. Small on purpose: we are measuring motion, not detail,
# and a small frame makes the per-pixel work cheap enough to stay in plain
# python on a machine with no numpy.
W, H = 192, 108
BW = 12                     # block size for the "busiest block" measure

VERSION = "3.1"


# --------------------------------------------------------------------------
# repair filenames - nothing already on disk is ever written over
# --------------------------------------------------------------------------
def even_versions(src):
    """Every repair sitting beside src, oldest first. Empty if there are none.

    The first repair of a file is <name>_even.mp4; the second is
    <name>_even_2.mp4, and so on. Older ones are kept so a new repair can
    always be compared against the one it replaced.
    """
    stem, ext = os.path.splitext(src)
    out = []
    first = f"{stem}_even{ext}"
    if os.path.exists(first):
        out.append(first)
    i = 2
    while os.path.exists(f"{stem}_even_{i}{ext}"):
        out.append(f"{stem}_even_{i}{ext}")
        i += 1
    return out


def free_even_output(src):
    """The filename the next repair should use. Never one that already exists."""
    stem, ext = os.path.splitext(src)
    if not os.path.exists(f"{stem}_even{ext}"):
        return f"{stem}_even{ext}"
    i = 2
    while os.path.exists(f"{stem}_even_{i}{ext}"):
        i += 1
    return f"{stem}_even_{i}{ext}"



# --------------------------------------------------------------- ffmpeg glue

def _rate_flag():
    """ffmpeg 8 removed -vsync; ffmpeg 4 has no -fps_mode. Pick what this one has.

    Both mean: give me exactly the frames the filter chain produced, do not
    duplicate or drop any to hit an output frame rate. Getting this wrong makes
    ffmpeg itself insert the very repeats we are trying to measure.
    """
    for flag in (["-fps_mode", "passthrough"], ["-vsync", "0"], []):
        try:
            r = subprocess.run(["ffmpeg", "-hide_banner", "-v", "error", "-f", "lavfi",
                                "-i", "nullsrc=s=32x32:d=0.05"] + flag + ["-f", "null", "-"],
                               capture_output=True)
            if r.returncode == 0:
                return flag
        except FileNotFoundError:
            return []
    return []


RATE = _rate_flag()


class Unreadable(Exception):
    pass


def _ffprobe(path, args):
    try:
        r = subprocess.run(["ffprobe", "-v", "error"] + args + [path], capture_output=True)
    except FileNotFoundError:
        raise Unreadable("ffprobe is not installed, or not on PATH. Run 0-SETUP-CHECK.bat.")
    return r.stdout.decode("utf-8", "replace")


# ------------------------------------------------------------- 1. container

class Container:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def probe(path):
    """Everything the file itself will tell us, before we look at a pixel."""
    if not os.path.exists(path):
        raise Unreadable("that file does not exist at the path given")
    if os.path.isdir(path):
        raise Unreadable("that is a folder, not a video file")
    if os.path.getsize(path) == 0:
        raise Unreadable("that file is empty (0 bytes)")

    txt = _ffprobe(path, ["-select_streams", "v:0", "-show_entries",
                          "stream=width,height,codec_name,r_frame_rate,avg_frame_rate,"
                          "nb_frames,duration", "-of", "default=nw=1"])
    d = {}
    for line in txt.strip().splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            d[k] = v.strip()

    def rat(s):
        try:
            n, den = s.split("/")
            return int(n) / int(den) if int(den) else None
        except Exception:
            return None

    fps = rat(d.get("avg_frame_rate", "")) or rat(d.get("r_frame_rate", ""))
    has_audio = "audio" in _ffprobe(path, ["-select_streams", "a", "-show_entries",
                                           "stream=codec_type", "-of", "csv=p=0"])
    tags = _ffprobe(path, ["-show_entries", "format_tags:stream_tags", "-of", "default=nw=1"])

    # Frame timestamps. An AI aggregator that muxes a variable timeline hands you
    # a clip that stutters with no pixel defect at all - the fix there is a
    # re-mux, not an interpolator. Cheap to check, and it saves inventing frames
    # to solve a problem that lives in the timestamps.
    pts = []
    try:
        t = _ffprobe(path, ["-select_streams", "v:0", "-show_entries",
                            "frame=pkt_pts_time,best_effort_timestamp_time",
                            "-of", "csv=p=0", "-read_intervals", "%+#400"])
        for line in t.strip().splitlines():
            for cell in line.split(","):
                cell = cell.strip()
                if cell and cell not in ("N/A",):
                    try:
                        pts.append(float(cell))
                    except ValueError:
                        pass
                    break
    except Exception:
        pts = []
    vfr = False
    spacing = []
    if len(pts) > 12:
        pts = sorted(pts)
        spacing = [round(pts[i] - pts[i - 1], 5) for i in range(1, len(pts))]
        uniq = sorted(set(spacing))
        if len(uniq) > 1 and (max(spacing) - min(spacing)) > 0.25 * statistics.median(spacing):
            vfr = True

    # Encoded bytes per displayed frame. A frame that is a copy of the one before
    # it costs the encoder almost nothing, so a near-empty packet is strong,
    # independent evidence of a repeat - evidence that does not come from the
    # same pixel measurement as everything else. Optional: some builds and some
    # containers will not report it, and we carry on without it.
    sizes, kinds = [], []
    try:
        t = _ffprobe(path, ["-select_streams", "v:0", "-show_entries",
                            "frame=pkt_size,pict_type", "-of", "csv=p=0"])
        for line in t.strip().splitlines():
            a = line.split(",")
            if len(a) >= 2:
                try:
                    sizes.append(int(a[0]))
                except ValueError:
                    sizes.append(0)
                kinds.append(a[1].strip())
    except Exception:
        sizes, kinds = [], []

    return Container(
        path=path,
        width=int(d.get("width") or 0), height=int(d.get("height") or 0),
        codec=d.get("codec_name", "?"),
        fps=fps,
        duration=float(d["duration"]) if d.get("duration") not in (None, "", "N/A") else None,
        nb_frames=int(d["nb_frames"]) if d.get("nb_frames", "").isdigit() else None,
        has_audio=has_audio, tags=tags, vfr=vfr, pts_spacing=spacing,
        pkt_sizes=sizes, pkt_kinds=kinds,
    )


# --------------------------------------------------------------- 2. signals

def _decode_gray(path, w=W, h=H):
    try:
        r = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-vf", f"scale={w}:{h}",
                            *RATE, "-pix_fmt", "gray", "-f", "rawvideo", "-"],
                           capture_output=True)
    except FileNotFoundError:
        raise Unreadable("ffmpeg is not installed, or not on PATH. Run 0-SETUP-CHECK.bat.")
    n = w * h
    fr = [r.stdout[i * n:(i + 1) * n] for i in range(len(r.stdout) // n)]
    if not fr:
        msg = (r.stderr or b"").decode("utf-8", "replace").strip().splitlines()
        detail = msg[-1] if msg else "no video frames came out of ffmpeg"
        ext = os.path.splitext(path)[1].lower()
        if ext in (".png", ".jpg", ".jpeg", ".bmp", ".gif", ".txt", ".md", ".bat", ".py"):
            raise Unreadable(f"that is not a video file ({ext})")
        raise Unreadable(f"ffmpeg could not decode it - {detail}")
    return fr


class Signals:
    """Three numbers per step, plus brightness per frame.

    step   mean absolute change over the whole frame. The workhorse, but on its
           own it cannot separate "a small thing moved fast" from "the whole
           frame drifted slightly", and both of those from a repeat.
    cstep  the same thing at half the resolution, and the reason is worth
           knowing. A mean absolute difference SATURATES: once a moving subject
           has travelled far enough that its two positions no longer overlap,
           moving twice as far adds almost nothing to the number. On a fast
           shot a missing frame therefore stops looking like a doubled step.
           Measured on a clip with frames deliberately removed, the same five
           gaps read 1.64 / 1.32 / 1.51 / 1.46 / 1.45 at full analysis size and
           1.99 / 1.53 / 1.57 / 1.54 / 1.53 at half - one of them invisible at
           the fine scale and obvious at the coarse one. Halving the resolution
           halves the displacement in pixels and puts the measurement back in
           its proportional range. So repeats are judged on the fine detail,
           where small movement shows, and missing frames on the coarse, where
           large movement still counts.
    peak   the largest change in any 12x12 block. A true repeat has a peak of
           roughly zero everywhere. A slow pan does not: something moved, even
           if the frame average is small. This is what stops slow passages from
           being read as padding.
    lum    mean brightness per frame. A fade or a lightning flash produces a big
           step with no motion in it; without this the tool tries to "restore a
           missing frame" into a flash.
    """

    def __init__(self, step, cstep, peak, lum, n):
        self.step, self.cstep, self.peak, self.lum, self.n = step, cstep, peak, lum, n


def signals(path):
    fr = _decode_gray(path)
    n = len(fr)
    if n < 8:
        raise Unreadable(f"only {n} frame(s) - too short to analyse (a still image looks like this)")

    if _np is not None:
        a = _np.frombuffer(b"".join(fr), dtype=_np.uint8).reshape(n, H, W).astype(_np.int16)
        diff = _np.abs(a[1:] - a[:-1])
        step = (diff.mean(axis=(1, 2))).tolist()
        bh, bw = H // BW, W // BW
        blk = diff[:, :bh * BW, :bw * BW].reshape(n - 1, bh, BW, bw, BW).mean(axis=(2, 4))
        peak = blk.max(axis=(1, 2)).tolist()
        lum = a.mean(axis=(1, 2)).tolist()
        c = a.reshape(n, H // 2, 2, W // 2, 2).mean(axis=(2, 4))
        cstep = _np.abs(c[1:] - c[:-1]).mean(axis=(1, 2)).tolist()
    else:
        step, peak, lum, coarse = [], [], [], []
        for f in fr:
            lum.append(sum(f) / (W * H))
            # half-resolution copy, made by averaging 2x2 blocks - the same
            # thing as decoding at half size, without a second decode
            c = []
            for y in range(0, H - 1, 2):
                r0, r1 = y * W, (y + 1) * W
                c.extend((f[r0 + x] + f[r0 + x + 1] + f[r1 + x] + f[r1 + x + 1]) / 4.0
                         for x in range(0, W - 1, 2))
            coarse.append(c)
        cn = len(coarse[0]) or 1
        cstep = [sum(abs(x - y) for x, y in zip(coarse[i], coarse[i - 1])) / cn
                 for i in range(1, n)]
        for i in range(1, n):
            a, b = fr[i], fr[i - 1]
            d = bytes(abs(x - y) for x, y in zip(a, b))
            step.append(sum(d) / (W * H))
            best = 0.0
            for by in range(0, H - BW + 1, BW):
                for bx in range(0, W - BW + 1, BW):
                    t = 0
                    for y in range(by, by + BW):
                        base = y * W
                        t += sum(d[base + bx:base + bx + BW])
                    v = t / (BW * BW)
                    if v > best:
                        best = v
            peak.append(best)
    return Signals(step, cstep, peak, lum, n)


# ---------------------------------------------------------- 3. segmentation

def _local_median(vals, i, half, skip=None):
    lo, hi = max(0, i - half), min(len(vals), i + half + 1)
    pool = [vals[j] for j in range(lo, hi) if j != i and (skip is None or not skip[j])]
    if not pool:
        pool = [vals[j] for j in range(lo, hi) if j != i] or [vals[i]]
    return statistics.median(pool)


def _runs(flags):
    """[(start, end_inclusive)] for each run of True."""
    out, s = [], None
    for i, f in enumerate(flags):
        if f and s is None:
            s = i
        elif not f and s is not None:
            out.append((s, i - 1)); s = None
    if s is not None:
        out.append((s, len(flags) - 1))
    return out


class Analysis:
    pass


def measure(path, sig=None, con=None):
    """Turn raw signals into the labelled facts everything else reasons from."""
    con = con or probe(path)
    sig = sig or signals(path)
    step, peak, lum, cstep = sig.step, sig.peak, sig.lum, sig.cstep
    ns = len(step)
    med = statistics.median(step) or 1e-9

    # --- repeats -------------------------------------------------------------
    # Three independent votes, because any single test has a clip that beats it.
    # A step needs two to be called a repeat.
    #
    #   peak     nothing anywhere in the frame moved      (the decisive one)
    #   packet   the encoder found nothing to encode      (independent of pixels)
    #   step     the frame average barely moved, judged
    #            against the MOVING steps nearby          (survives slow passages)
    prov = [v < 0.45 * med for v in step]
    rel, prel = [], []
    pmedall = statistics.median(peak) or 1e-9
    for i, v in enumerate(step):
        base = _local_median(step, i, 12, skip=prov) or med
        rel.append(v / base if base > 0 else 0.0)
        pbase = _local_median(peak, i, 12, skip=prov) or pmedall
        prel.append(peak[i] / pbase if pbase > 0 else 0.0)

    # The packet vote, compared WITHIN a picture type.
    #
    # An earlier version compared every frame against one median and called a
    # repeat anything under a tenth of it. On a re-encoded h264 file with
    # B-frames that never fires: a repeated frame there still costs 20-50 kB,
    # because it is being coded against a future reference as well as a past
    # one, and a P-frame costs three times what a B-frame costs whatever is in
    # it. So the vote was dead, and a test that needed two votes out of three
    # was quietly running on two out of two.
    pkt_vote = [False] * ns
    sizes, kinds = con.pkt_sizes, con.pkt_kinds
    if len(sizes) >= sig.n and sig.n > 0:
        by_kind = {}
        for s, k in zip(sizes, kinds):
            by_kind.setdefault(k, []).append(s)
        kmed = {k: (statistics.median(vv) or 1) for k, vv in by_kind.items()}
        for i in range(ns):
            j = i + 1                       # step i is the arrival of frame i+1
            if j < len(sizes) and kinds[j] != "I":
                if sizes[j] < 0.55 * kmed.get(kinds[j], 1):
                    pkt_vote[i] = True

    # `peak` is the decisive measurement and is now treated as such.
    #
    # On a real padded repeat, the busiest block in the frame moves by less than
    # a grey level, while the moving frames around it move by four to eleven -
    # a separation of five to twenty times. Nothing else here comes close to
    # that. The frame average is far weaker: on `walking forest.mp4` the repeats
    # measure 0.09 to 0.35 of their neighbours, and a flat cutoff at 0.22 caught
    # six of the ten. The beat then broke into fragments, no chain reached four,
    # and an untouched download was accused of having been repaired already.
    #
    #   A  the busiest block barely moved, RELATIVE to the moving frames nearby
    #   B  the busiest block barely moved at all, in absolute terms
    #   C  the frame average is far below the moving frames nearby
    #   D  the encoder found little to code, against others of its own type
    #
    # A and B together are conclusive. Otherwise the peak evidence still has to
    # be there, plus one corroboration - which is what stops a slow, low
    # contrast passage, where everything is quiet, from reading as padding.
    dup = []
    for i in range(ns):
        A = prel[i] < 0.30
        B = peak[i] <= 1.6
        C = rel[i] < 0.30
        D = pkt_vote[i]
        dup.append((A and B) or (A and (C or D)) or (B and C))

    # --- luminance events ----------------------------------------------------
    # A fade or a flash: brightness moved a lot AND that is most of what
    # changed. The second half of that sentence is the important half.
    #
    # Testing only "did the brightness move a lot" throws away real defects on
    # any shot that gets brighter as it goes - a push-in towards a light source,
    # a camera coming out of shade. On the test clip that rule flagged three
    # steps, two of which were frames deliberately removed, and the tool then
    # skipped over its own damage and reported the clip as fine.
    #
    # The discriminator is the RATIO. In a fade every pixel shifts the same way,
    # so the mean change is nearly the whole frame difference. In motion, pixels
    # move both ways and cancel: across this clip the ratio runs 0.05 at the
    # median and never exceeds 0.34, while a fade sits near 1.0.
    dl = [abs(lum[i + 1] - lum[i]) for i in range(ns)]
    dlmed = statistics.median(dl) or 1e-9
    flash = [dl[i] > max(3.0, 5 * dlmed) and dl[i] > 0.5 * max(step[i], 1e-9)
             for i in range(ns)]

    # --- cuts ----------------------------------------------------------------
    # A hard cut inside a generated clip is common and is not a defect. It must
    # never be interpolated across: doing so invents a frame that is half of one
    # shot and half of another, which is the single ugliest thing these tools
    # can produce.
    cuts = []
    for i in range(ns):
        base = _local_median(step, i, 12, skip=dup) or med
        if step[i] > 5.0 * base and peak[i] > 45:
            cuts.append(i)

    a = Analysis()
    a.con, a.sig = con, sig
    a.n, a.ns = sig.n, ns
    a.step, a.peak, a.lum, a.cstep = step, peak, lum, cstep
    a.dup, a.rel, a.prel, a.flash, a.cuts = dup, rel, prel, flash, cuts
    a.pkt_vote = pkt_vote
    a.med = med
    return a


def segment(a):
    """Split into (cut-free) segments, then inside each mark holds and active runs.

    A HOLD is a run of three or more repeats in a row: the scene genuinely
    stopped. Painting motion into a held pose looks far worse than the stutter
    you were trying to remove, so holds are copied through untouched and, just
    as importantly, are kept out of every average and every frame-rate sum.

    Three and not two, because a run of exactly two is usually an accident: a
    padded repeat that happens to land next to a quiet moment. Splitting the
    clip there cuts a perfectly good padded stretch in half and can leave the
    fragments too short to recognise as padding at all.
    """
    a.segments = []
    bounds = [0] + [c + 1 for c in a.cuts] + [a.n]
    bounds = sorted(set(bounds))
    for s, e in zip(bounds, bounds[1:]):
        if e - s < 2:
            continue
        # step indices belonging to this segment: steps s .. e-2
        lo, hi = s, e - 2
        if hi < lo:
            continue
        holds = [(r[0], r[1]) for r in _runs(a.dup)
                 if r[0] >= lo and r[1] <= hi and r[1] - r[0] >= 2]
        held = [False] * a.ns
        for x, y in holds:
            for i in range(x, y + 1):
                held[i] = True
        active, cur = [], None
        for i in range(lo, hi + 1):
            if not held[i] and cur is None:
                cur = i
            elif held[i] and cur is not None:
                active.append((cur, i - 1)); cur = None
        if cur is not None:
            active.append((cur, hi))
        a.segments.append(dict(frames=(s, e - 1), steps=(lo, hi), holds=holds, active=active))
    return a


# --------------------------------------------------------- 4. classification

def _pad_evidence(a, lo, hi):
    """Isolated repeats that land on an exact beat.

    Isolated is the whole point: one repeat with real motion either side is a
    padded frame; a run of them is a held pose. Chains rather than a global
    phase, because padding is often only part of a clip and because generators
    that stitch segments together let the beat slip mid-shot - which ends one
    chain and starts another instead of destroying the answer.
    """
    iso = [i for i in range(lo, hi + 1) if a.dup[i]
           and not (i > lo and a.dup[i - 1]) and not (i < hi and a.dup[i + 1])]
    best = dict(period=None, members=[], cover=0.0)
    if len(iso) < 4:
        return best
    span = hi - lo + 1
    for P in (2, 3, 4, 5):
        # A chain may step over ONE missed repeat.
        #
        # Requiring every link to be exactly P made the whole answer hostage to
        # the weakest single measurement in the clip: miss one repeat in the
        # middle of a run of ten and the chain splits into two runs of four and
        # five, and if the bar is four, one of them is thrown away. Allowing a
        # 2P link keeps the beat intact and still stays exactly on the lattice.
        # Bridges have to stay outnumbered by solid links two to one, so a
        # sparse scatter cannot be strung together into a beat that is not
        # there - and in particular so period 2 cannot bridge its way through a
        # genuine period-4 pattern.
        chains, cur, solid, bridge = [], [iso[0]], 0, 0
        cs, cb = 0, 0
        for x, y in zip(iso, iso[1:]):
            if y - x == P:
                cur.append(y); cs += 1
            elif y - x == 2 * P:
                cur.append(y); cb += 1
            else:
                chains.append((cur, cs, cb)); cur, cs, cb = [y], 0, 0
        chains.append((cur, cs, cb))
        members = [i for c, s, b in chains if len(c) >= 4 and s >= 2 * b for i in c]
        if not members:
            continue
        reach = max(members) - min(members) + P
        cover = len(members) / max(1.0, reach / P)          # how solid the beat is
        weight = len(members)
        if weight > len(best["members"]) or (weight == len(best["members"]) and P > (best["period"] or 0)):
            best = dict(period=P, members=members, cover=cover)
    return best


# How big is a step with a frame missing out of it, really?
#
# Not 2.0. That was the assumption these tools were built on and it is wrong.
# The measurement is a mean absolute pixel difference, and that saturates: when
# a subject moves twice as far between two frames, the pixels it uncovers do not
# double, because the two positions stop overlapping at all. Measured against
# ground truth - a clean clip with individual frames deliberately removed - a
# genuinely missing frame reads 1.48 to 1.65, while nothing in the untouched
# clip exceeds 1.30.
#
# Those two numbers are what set the bars below. Waiting for 2.0 is why real
# defects were being reported as "nothing to repair".
# Both bars are set on the COARSE measurement, which is why they sit a little
# higher than the fine-scale numbers above: on a clip with nothing wrong the
# coarse signal tops out around 1.40, and on one with frames removed the gaps
# read 1.53 and up.
SPIKE_FLOOR = 1.40          # above anything a clean clip produces
SEAM_STRENGTH = 1.50        # a step this size has a frame missing out of it


DOUBLED = 1.25              # a step this much bigger than its neighbours is carrying two frames


def _pad_phase(a, members, lo, hi, reach=3):
    """How far after each repeat the doubled step actually sits.

    Returns 1 - the ordinary geometry - unless some other offset reads as
    doubled across the whole region while pd+1 does not. Measured over every
    repeat at once, because the generator's phase is a property of the shot and
    holds for all of them.

    Two real clips, both PAD period 4, measured as the median of
    step / local-neighbour-size at each offset:

        pd+1   pd+2   pd+3
        1.52   0.82   0.79     the copy sits where the moment was  -> phase 1
        0.72   1.60   0.83     an ordinary frame sits between them -> phase 2

    The separation is wide and it is consistent, so this needs no tuning knob.
    """
    scored = {}
    for off in range(1, reach + 1):
        rr = [a.cstep[pd + off] / max(_near_base(a, pd + off, lo, hi), 1e-9)
              for pd in members
              if lo <= pd + off <= hi and not a.dup[pd + off] and not a.flash[pd + off]]
        if len(rr) >= max(3, len(members) // 2):
            scored[off] = statistics.median(rr)
    if not scored:
        return 1
    if scored.get(1, 0.0) >= DOUBLED:
        return 1                    # pd+1 pays for the repeat: the usual case
    best = max(scored, key=lambda o: scored[o])
    if scored[best] < DOUBLED:
        return 1                    # nothing here reads as doubled; keep the default
    return best


def _pad_doubles(a, members, period, lo, hi):
    """Pair every padded repeat with the moment it replaced.

    This is the arithmetic that makes the padded case exact rather than
    approximate. In a fixed-rate conversion each repeat pays for exactly one
    lost moment, so the number of doubled steps is KNOWN once the repeats are
    counted - no threshold to get wrong, and the output lands on the original
    frame count by construction.

    The pairing is LOCAL. Taking the N biggest steps in the clip instead would
    dump every restored moment into the fastest passage, leaving the quiet parts
    stretched and the busy parts compressed. The total length still comes out
    right, so it looks correct on paper while the picture slides against the
    sound.

    Returns (pairs, unpaired). A repeat that cannot be given a partner is handed
    back as unpaired and is then NOT treated as padding at all - because if no
    doubled step nearby paid for it, the evidence that a frame was lost there is
    not present, and pretending otherwise costs far more than it sounds. The
    arithmetic only closes when the counts match: one repeat short and the span
    no longer lands on whole frames, so every frame in it becomes a fractional
    position and gets repainted. That is how a clip that needed 41 new frames
    ended up having 166 of its 193 frames re-rendered for the sake of one
    unmatched repeat.
    """
    pairs, taken = {}, set()
    order = sorted(members)

    # The partner is USUALLY the very next step, and that much is geometry: the
    # padded frame is a copy of the one before it, so the step INTO it carries no
    # motion and the step OUT of it has to carry two frames' worth - the moment
    # the copy replaced, and the moment that follows.
    #
    # It is not always the next step, though, and taking it on faith is a real
    # bug rather than a tidy simplification. Some generators leave an ordinary
    # frame between the copy and the doubled step, so pd+1 is a perfectly
    # healthy step and the hitch that has to be paid for sits at pd+2. Pairing
    # to pd+1 there hands the restored moment to a step that never lost
    # anything: the repair splits a healthy pair, the freeze comes out of the
    # clip and the lurch stays in. Frame count, rate and duration all come back
    # perfect, the engine stops naming the defect, and the clip still stutters -
    # which is the worst shape a failure can take, because everything says it
    # worked.
    #
    # So measure the offset instead of assuming it. The phase belongs to the
    # generator, not to any one repeat, so it is decided once for the whole
    # region from all the repeats together - one repeat's neighbourhood is noisy,
    # forty of them are not - and then applied to every repeat in it. Ranking a
    # window by raw size per repeat was tried and is worse: on a shot that speeds
    # up, a later ordinary step can outweigh an earlier doubled one.
    phase = _pad_phase(a, order, lo, hi)
    for pd in order:
        nxt = pd + phase
        if lo <= nxt <= hi and not a.dup[nxt] and not a.flash[nxt] and nxt not in taken:
            pairs[pd] = nxt; taken.add(nxt)
    for width in (period, 2 * period):          # then wider, for any leftovers
        for pd in order:
            if pd in pairs:
                continue
            win = [i for i in range(max(lo, pd - width), min(hi + 1, pd + width + 1))
                   if not a.dup[i] and i not in taken and not a.flash[i]]
            if win:
                pick = max(win, key=lambda i: a.cstep[i] / max(_near_base(a, i, lo, hi), 1e-9))
                pairs[pd] = pick; taken.add(pick)
    unpaired = [pd for pd in order if pd not in pairs]
    return pairs, unpaired


def _near_base(a, i, lo, hi, per_side=2, reach=6):
    """The size a step at i OUGHT to be, from its closest real neighbours.

    This replaced a median over a window of twenty-one steps, which quietly
    fails on two very common shots.

    A shot that ACCELERATES defeats it: on the pyramid clip the steps climb from
    2.0 to over 12 across four seconds, so a wide window mixes early steps with
    late ones and the median lands nowhere near the local level.

    And a padded clip defeats it from the other side: with one frame in four
    repeated, a third of the surviving steps are the doubles themselves, so they
    drag the median up towards their own size and stop standing out. Both at
    once - which is this clip - is fatal. A doubled step measuring 4.46 against
    neighbours of 2.7 and 3.0, plainly twice their size, was being scored 1.05
    and dismissed.

    Two real steps either side is enough to say what the local level is, and
    close enough that acceleration cancels out of it.
    """
    vals = []
    for d in (-1, 1):
        got, j = 0, i + d
        while got < per_side and lo <= j <= hi and abs(j - i) <= reach:
            if not a.dup[j] and not a.flash[j] and j not in a.cuts:
                vals.append(a.cstep[j]); got += 1
            j += d
    if not vals:
        return _local_median(a.cstep, i, 10, skip=a.dup) or (statistics.median(a.cstep) or 1e-9)
    return statistics.median(vals)


def _spikes(a, lo, hi):
    """Steps that stand up as a SPIKE against their neighbours.

    A missing frame is a lone doubled step: the frame before it and the frame
    after it are ordinary. Fast motion does not look like that - it ramps up
    and back down, so the neighbours are raised too. Testing for the spike
    shape, and not just for "bigger than average", is what keeps an action beat
    from being read as six missing frames.
    """
    out = []
    for i in range(lo, hi + 1):
        if a.dup[i] or a.flash[i] or i in a.cuts:
            continue
        base = _near_base(a, i, lo, hi)
        if base <= 0:
            continue
        r = a.cstep[i] / base
        if r < SPIKE_FLOOR:
            continue
        left = a.cstep[i - 1] / base if i - 1 >= lo else 1.0
        right = a.cstep[i + 1] / base if i + 1 <= hi else 1.0
        isolated = left < 1.25 and right < 1.25
        out.append(dict(i=i, ratio=r, left=left, right=right, spike=isolated))
    return out


SERIES_FLOOR = 1.15     # a lower bar, allowed ONLY where an established beat predicts a spike
SERIES_MIN_PERIOD = 8   # a real "once a second" beat, not the dense cycle of a pulldown


def _extend_series(a, out, lo, hi):
    """Fill in members of an evenly spaced run of spikes that fell just short.

    A generator that drops a frame does it on a beat - once a second, say - so the
    spikes come out evenly spaced. How BIG each one measures depends on how fast
    the picture happens to be moving at that moment, so on a slower passage one
    beat lands under the threshold and is missed while its neighbours are found.
    The clip then comes back with one skip still in it, in a stretch the tool
    reported as repaired, which is the worst way to be wrong.

    Measured on a real clip: spikes at 47, 71 and 95, beat of 24, and the fourth
    member at 23 reading 1.29 against a floor of 1.40. Missed by 0.11, and plainly
    visible to the person watching it.

    So where a beat is ESTABLISHED - three or more spikes, evenly spaced, spaced
    widely enough not to be a pulldown's cycle - the bar comes down at the
    positions that beat predicts, and nowhere else. The evidence for those frames
    is not their own size; it is the series they belong to.
    """
    strong = sorted(s["i"] for s in out if s.get("spike"))
    if len(strong) < 3:
        return out
    diffs = [b - c for c, b in zip(strong, strong[1:])]
    if not diffs:
        return out
    P = statistics.median(diffs)
    if P < SERIES_MIN_PERIOD:
        return out
    if any(d < 1 or abs(d / P - round(d / P)) > 0.12 for d in diffs):
        return out
    P = int(round(P))

    predicted = []
    x = strong[0] - P
    while x >= lo:
        predicted.append(x); x -= P
    x = strong[-1] + P
    while x <= hi:
        predicted.append(x); x += P
    for c, b in zip(strong, strong[1:]):          # and any beat skipped in the middle
        for m in range(1, int(round((b - c) / P))):
            predicted.append(c + m * P)

    have = {s["i"] for s in out}
    added = 0
    for i0 in sorted(set(predicted)):
        best = None
        for i in (i0, i0 - 1, i0 + 1):            # the beat can drift by a frame
            if not (lo <= i <= hi) or i in have:
                continue
            if a.dup[i] or a.flash[i] or i in a.cuts:
                continue
            base = _near_base(a, i, lo, hi)
            if base <= 0:
                continue
            r = a.cstep[i] / base
            if r >= SERIES_FLOOR and (best is None or r > best[1]):
                best = (i, r, base)
        if best:
            i, r, base = best
            left = a.cstep[i - 1] / base if i - 1 >= lo else 1.0
            right = a.cstep[i + 1] / base if i + 1 <= hi else 1.0
            out.append(dict(i=i, ratio=r, left=left, right=right, spike=True,
                            from_series=True))
            have.add(i)
            added += 1
    if added:
        out.sort(key=lambda s: s["i"])
    return out


def _cycle_fit(idx, span):
    """Does this set of positions repeat on a fixed cycle?

    Not "are the gaps even" - they are not. A 30-into-24 conversion drops on a
    5,5,5,5,2,2 rhythm: lumpy, but perfectly periodic. The question that gets
    both cases right is whether another drop sits exactly one cycle later.
    """
    if len(idx) < 6:
        return 0.0, None
    S = set(idx)
    best, cyc = 0.0, None
    for C in range(3, min(41, max(4, span // 2))):
        pairs = [j for j in idx if j + C <= idx[-1]]
        if len(pairs) < 5:
            continue
        hit = sum(1 for j in pairs if (j + C) in S or (j + C - 1) in S or (j + C + 1) in S) / len(pairs)
        if hit > best:
            best, cyc = hit, C
    return best, cyc


STANDARD_RATES = (25.0, 30.0, 30000 / 1001, 48.0, 50.0, 60.0)


# --------------------------------------------------------- the periodic grid
#
# Added in 3.1. This was the largest untouched class in the library - roughly
# sixty clips in four hundred - and every version before this one called them
# CLEAN.
#
# The fault has no repeated frame anywhere, so nothing trips the repeat
# detector, and no step is doubled, so nothing trips the spike detector. What is
# there instead is an alternation: long step, short step, long step, across the
# whole frame, in strict phase, usually by 6 to 18%. It is what you get when a
# render at one rate is resampled onto another by an encoder that blends rather
# than drops, or when a generator's internal clock never matched its output.
# Small, and utterly regular, which is exactly what the eye reads as stutter.
#
# The test is a one-way ANOVA on the step sizes grouped by index modulo m. Real
# motion has no opinion about whether a frame's index is even or odd; a
# resampled clip has a very strong one. The F-statistic is the right tool
# because it asks the honest question - is the difference between the phases
# bigger than the noise inside them - and because it penalises the extra groups
# of a larger m on its own, so a 2-frame beat is not mistaken for a 4-frame one.

GRID_FLOOR = 0.08       # phases disagreeing by less than this are not worth repainting
GRID_F = 12.0           # between-phase variance this many times the within-phase noise
GRID_MIN = 24           # steps needed before the statistic means anything


def _grid_evidence(a, lo, hi):
    """Are the steps locked to a beat that is not the frame grid?

    Returns the best-fitting modulus with the size of the disagreement (as a
    fraction of the average step) and the F-statistic behind it.

    Repeats, flashes and cuts are left out of the groups but keep their place in
    the index, so the phase is never silently shifted by a frame.
    """
    blank = dict(m=None, amp=0.0, F=0.0, means=[], n=0)
    norm = {}
    for i in range(lo, hi + 1):
        if a.dup[i] or a.flash[i] or i in a.cuts:
            continue
        base = _local_median(a.step, i, 4, skip=a.dup)
        if base and base > 0:
            norm[i] = a.step[i] / base
    if len(norm) < GRID_MIN:
        return blank
    grand = statistics.fmean(norm.values())
    if grand <= 0:
        return blank
    best = blank
    for m in (2, 3, 4):
        groups = [[] for _ in range(m)]
        for i, v in norm.items():
            groups[(i - lo) % m].append(v)
        if min(len(g) for g in groups) < 6:
            continue
        means = [statistics.fmean(g) for g in groups]
        amp = (max(means) - min(means)) / grand
        between = sum(len(g) * (mu - grand) ** 2
                      for g, mu in zip(groups, means)) / (m - 1)
        within_ss = sum((v - mu) ** 2 for g, mu in zip(groups, means) for v in g)
        dfw = len(norm) - m
        within = within_ss / dfw if dfw > 0 else 0.0
        F = between / within if within > 1e-12 else 0.0
        if F > best["F"]:
            best = dict(m=m, amp=round(amp, 4), F=round(F, 2),
                        means=[round(x, 3) for x in means], n=len(norm))
    return best


def _grid_advance(a, lo, hi, m):
    """How far motion time really advances at each step of a GRID region.

    One number per step, scaled so the average over the region is exactly 1.0.
    That scaling is not cosmetic: it is what guarantees the region holds the
    same amount of motion time as it holds frames, so the repair can only ever
    redistribute the existing frames and can never change the length of the
    clip. The individual values are clamped as well - a step that reads three
    times its neighbours is a piece of fast motion, not a grid fault, and
    stretching the clip around it would be worse than the stutter.

    The value used is the phase mean rather than the step itself. The phase is
    the part that repeats and is therefore the part that is a defect; the rest
    of the variation is the motion in the shot, which is meant to be there.
    """
    vals, idx = {}, []
    for i in range(lo, hi + 1):
        base = _local_median(a.step, i, 4, skip=a.dup)
        if a.dup[i] or a.flash[i] or i in a.cuts or not base or base <= 0:
            continue
        vals[i] = a.step[i] / base
        idx.append(i)
    if not idx:
        return {}
    groups = [[] for _ in range(m)]
    for i in idx:
        groups[(i - lo) % m].append(vals[i])
    means = [statistics.fmean(g) if g else 1.0 for g in groups]

    # Normalise the PHASE MEANS, not the whole series.
    #
    # This is not a rounding detail, it is the difference between the repair
    # working and not working. Scaling every step so the region's total comes to
    # 1.0 per frame leaves each COMPLETE CYCLE short by a fraction, because the
    # region rarely holds a whole number of cycles and the phases are of unequal
    # length. A fifteen-hundredth of a frame per cycle is nothing; eighty cycles
    # of it is an eighth of a frame, which is larger than the fault being
    # repaired. The correction then starts out right, drifts through the clip,
    # and is repairing the wrong frames by the end of it - measured on a test
    # clip, that halved the fault instead of removing it.
    #
    # Making the phase means average exactly 1.0 makes every cycle advance
    # exactly m, so there is nothing to accumulate.
    scale = statistics.fmean(means)
    if scale <= 0:
        return {}
    means = [min(1.6, max(0.4, x / scale)) for x in means]
    return {i: means[(i - lo) % m] for i in range(lo, hi + 1)}


def classify_region(a, lo, hi):
    """One active run of steps -> one class, with the evidence that chose it.

    Every hypothesis is scored, and the winner has to beat the runner-up by a
    margin. When nothing wins clearly the answer is AMBIGUOUS and the repair
    stops. Guessing is exactly how the wrong fix gets applied.
    """
    span = hi - lo + 1
    r = dict(lo=lo, hi=hi, span=span, klass="CLEAN", scores={}, why=[],
             pad=dict(period=None, members=[], cover=0.0), spikes=[], all_candidates=[],
             cycle_fit=0.0, cycle=None, implied_rate=0.0, moving=0,
             defect=(lo, hi + 1))
    if span < 10:
        r["klass"] = "CLEAN"
        r["why"].append("too short to judge; left alone")
        return r

    pad = _pad_evidence(a, lo, hi)
    sp = _extend_series(a, _spikes(a, lo, hi), lo, hi)
    strong = [s for s in sp if s["spike"]]
    idx = [s["i"] for s in strong]
    fit, cyc = _cycle_fit(idx, span)

    # Where does the defect actually reach?
    #
    # This matters more than it looks. `mountain 2` drops frames from the start
    # until frame 170 and is native 24 fps after that. Measured across the whole
    # clip the implied source rate comes out at 28.78 fps - a rate no generator
    # produces - and the repair then re-renders 46 perfectly good frames. Measured
    # across the stretch that is actually damaged it comes out at exactly 30.00,
    # and those 46 frames are left alone.
    if idx:
        d_lo, d_hi = max(lo, min(idx)), min(hi, max(idx))
    else:
        d_lo, d_hi = lo, hi
    dspan = d_hi - d_lo + 1
    moving = dspan - sum(1 for i in range(d_lo, d_hi + 1) if a.dup[i])
    n_drop = len(strong)
    rate = 24.0 * (moving + n_drop) / moving if moving else 0.0
    near_std = min((abs(rate - s) / s for s in STANDARD_RATES), default=1.0)
    snap = min(STANDARD_RATES, key=lambda s: abs(s - rate)) if rate else 0.0
    spread = (max(idx) - min(idx)) / span if len(idx) > 1 else 0.0

    # --- score PAD ---
    s_pad = 0.0
    if pad["period"] and len(pad["members"]) >= 5:
        s_pad = min(1.0, 0.45 + 0.55 * min(1.0, pad["cover"]))
        if len(pad["members"]) < 8:
            s_pad *= 0.8
    r["scores"]["PAD"] = round(s_pad, 3)

    # --- score PULLDOWN ---
    # frames thrown away throughout, on a repeating cycle. Needs a believable
    # source rate as well as a repeating pattern - "every 7th frame is missing"
    # implies a 28 fps original, which no generator produces.
    #
    # The strongest evidence by far is that the arithmetic lands on a rate a
    # generator actually produces. "6 frames missing out of 137" is a guess;
    # "6 frames missing out of 137, which is 25.05 fps squeezed into 24" is a
    # finding. When that lands, the repair uses the SNAPPED rate rather than the
    # count, which is what makes it survive a drop the detector missed - a
    # missed drop becomes a fraction of a percent of speed error spread
    # smoothly over the clip, instead of a hitch left sitting in it.
    # PULLDOWN is claimed only when the drops form a CONFIRMED repeating cycle.
    # That matters, because the two repairs behave very differently when the
    # detector has missed something. A confirmed cycle means drops we did not
    # see are almost certainly there too, so the whole stretch is rebuilt from
    # the source rate and a missed drop costs a fraction of a percent of speed,
    # spread smoothly. Without a cycle, the drops we found are all there is to
    # go on - and rebuilding the whole stretch from a guessed rate then moves
    # every frame slightly for no reason, which measurably makes the clip worse.
    # Those are handled as SEAM instead, one short window per hitch.
    s_pull = 0.0
    if n_drop >= 5 and fit >= 0.70 and 24.4 <= rate <= 62:
        s_pull = 0.45 + 0.30 * (fit - 0.70) / 0.30
        if near_std <= 0.05:
            s_pull += 0.25 * (1.0 - near_std / 0.05)
        if spread >= 0.6:
            s_pull += 0.05
        s_pull = min(1.0, s_pull)
    r["scores"]["PULLDOWN"] = round(s_pull, 3)

    # --- score SEAM ---
    # a few isolated missing frames, well separated, each genuinely close to a
    # doubled step. This is the class the old tools had no branch for: too few
    # and too irregular to look like a rate conversion, but real, and visible
    # as a hitch about once a second.
    gaps = [idx[i] - idx[i - 1] for i in range(1, len(idx))]
    ratios = [s["ratio"] for s in strong]
    s_seam = 0.0
    # Three, not two. Two lone steps a little over the bar is not a pattern -
    # it is two lone steps, and they are as likely to be a pair of quick moves
    # as a pair of dropped frames. Insisting on three is also what stops a
    # freshly repaired clip from being read as freshly broken: an optical-flow
    # midpoint sits a shade softer than a real frame, which leaves one or two
    # steps looking marginally large, and at a floor of two the tool would
    # diagnose its own good work as damage.
    if 3 <= n_drop <= max(3, span // 8) and (not gaps or min(gaps) >= 6):
        strength = statistics.median(ratios) if ratios else 0
        if strength >= SEAM_STRENGTH:
            s_seam = min(1.0, 0.45 + 0.55 * min(1.0, (strength - SEAM_STRENGTH) / 0.30))
            if len(strong) < len(sp):        # some candidates were ramps, not spikes
                s_seam *= 0.85
    r["scores"]["SEAM"] = round(s_seam, 3)

    # --- score GRID ---
    # No frame repeated and no step doubled, but the steps alternate on a strict
    # beat.
    #
    # Both of those conditions are tested, and the second one is not optional.
    # A clip losing a frame every ninth is periodic too - nine divides by three,
    # so the phases disagree strongly and the statistic lights up - but there is
    # nothing grid-like about it: frames are genuinely missing and a
    # redistribution would smear that damage across the clip instead of
    # repairing it. GRID is the residue class. It is claimed only where there is
    # neither a repeat nor a drop to explain what was measured.
    grid = _grid_evidence(a, lo, hi)
    r["grid"] = grid
    s_grid = 0.0
    if (grid["m"] and grid["amp"] >= GRID_FLOOR and grid["F"] >= GRID_F
            and n_drop <= 2
            and not (pad["period"] and len(pad["members"]) >= 5)):
        s_grid = min(1.0, 0.45 + 0.55 * min(1.0, (grid["amp"] - GRID_FLOOR) / 0.10))
        if grid["F"] < 2 * GRID_F:
            s_grid *= 0.9
    r["scores"]["GRID"] = round(s_grid, 3)

    # IRREGULAR is deliberately NOT a scored hypothesis. It is what is left when
    # nothing else earns its place - big steps that ramp up and down instead of
    # standing alone, which is what fast motion looks like. Giving it a score of
    # its own only ever produced spurious ties.
    r["ramp_fraction"] = round(1 - len(strong) / len(sp), 2) if sp else 0.0

    # --- does the winner stand on its own evidence? --------------------------
    #
    # Changed in 3.1, and this is the single change that recovers the most
    # clips. The rule used to be: if the runner-up is within 0.12 of the winner,
    # refuse the clip as AMBIGUOUS. The intent was right - never guess between
    # two readings - but the test was measuring the wrong thing, because
    # PAD, PULLDOWN and SEAM are NOT independent hypotheses.
    #
    # A padded clip IS a rate conversion. The repeats are how that particular
    # converter did it, so PULLDOWN scores high on every padded clip by
    # construction, and the two scores cannot separate what they both describe.
    # Six clips in sixty were refused on pairs like PAD 1.00 / PULLDOWN 0.89 and
    # SEAM 1.00 / PULLDOWN 0.98, and every one of them repaired correctly the
    # moment the class was named by hand.
    #
    # So the gate is now on the winner's OWN evidence. Repeats on a solid beat
    # are an observation; a handful of isolated doubled steps, well separated,
    # is an observation; an implied source rate that lands on a rate a generator
    # actually produces is an observation. The runner-up's score is used only to
    # break a genuine tie where NEITHER class has evidence of its own - which is
    # what AMBIGUOUS was always meant to mean.
    #
    # The order below is the order of directness. A repeat is seen. A doubled
    # step is seen. A source rate is arithmetic on what was seen, so it yields
    # to both. A grid is the residue: it is claimed only where there is neither
    # a repeat nor a drop to explain the clip.
    solid_pad = bool(pad["period"] and len(pad["members"]) >= 5
                     and pad["cover"] >= 0.65)
    solid_seam = bool(not solid_pad
                      and 3 <= n_drop <= max(3, span // 8)
                      and (not gaps or min(gaps) >= 6)
                      and ratios and statistics.median(ratios) >= SEAM_STRENGTH)
    solid_pull = bool(n_drop >= 5 and fit >= 0.80 and near_std <= 0.03)
    solid_grid = bool(s_grid >= 0.40 and not solid_pad and not solid_seam
                      and not solid_pull)
    r["evidence"] = dict(PAD=solid_pad, SEAM=solid_seam,
                         PULLDOWN=solid_pull, GRID=solid_grid)

    ranked = sorted(r["scores"].items(), key=lambda kv: -kv[1])
    top, second = ranked[0], (ranked[1] if len(ranked) > 1 else ("", 0.0))
    # a class only gets to argue from its own evidence if it is actually in
    # contention - it has to be the winner or within a whisker of it
    standing = [k for k in ("PAD", "SEAM", "PULLDOWN", "GRID")
                if r["evidence"][k] and r["scores"].get(k, 0.0) >= 0.40
                and r["scores"].get(k, 0.0) >= top[1] - 0.12]
    r["solid"] = False
    if top[1] < 0.40:
        # Three, again. One step measuring 1.41 against its neighbours is not
        # "bursts of fast motion", it is one step, and calling the clip
        # IRREGULAR over it is a distinction without a difference - both verdicts
        # mean the same thing, that nothing will be touched. It stopped mattering
        # only in wording until the self-test began refusing to use such a clip
        # as its known-good source.
        r["klass"] = "IRREGULAR" if len(sp) >= 3 else "CLEAN"
        r["why"].append("no explanation scores high enough; nothing repaired here")
    elif standing:
        r["klass"] = standing[0]
        r["solid"] = True
        if standing[0] != top[0]:
            r["why"].append(
                f"{standing[0]} and {top[0]} both score high, but they are two "
                f"descriptions of the same event; {standing[0]} is the one that "
                f"was directly measured")
    elif top[1] - second[1] < 0.12:
        r["klass"] = "AMBIGUOUS"
        r["why"].append(f"{top[0]} {top[1]:.2f} and {second[0]} {second[1]:.2f} fit "
                        f"about equally, and neither has evidence of its own")
    else:
        r["klass"] = top[0]

    r["pad"], r["spikes"] = pad, strong
    r["all_candidates"] = sp
    r["cycle_fit"], r["cycle"] = round(fit, 2), cyc
    r["implied_rate"] = round(rate, 2)
    # only claim a source rate when there is enough evidence to be claiming one;
    # otherwise the log fills up with "24 fps source -> 25" on clips that have
    # nothing wrong with them
    r["snap_rate"] = (round(snap, 3) if (n_drop >= 5 and near_std <= 0.05)
                      else round(rate, 3))
    r["near_std"] = round(near_std, 4)
    r["spread"] = round(spread, 2)
    r["moving"] = moving

    # the stretch of FRAMES the repair is allowed to touch for this region
    r["doubles"] = []
    if r["klass"] == "PULLDOWN":
        pad_out = max(2, (cyc or 6) // 3)
        r["defect"] = (max(lo, d_lo - pad_out), min(hi, d_hi + pad_out) + 1)
    elif r["klass"] == "PAD" and pad["members"]:
        P = pad["period"]
        p_lo = max(lo, min(pad["members"]) - P)
        p_hi = min(hi, max(pad["members"]) + P)
        r["defect"] = (p_lo, p_hi + 1)
        prs, unpaired = _pad_doubles(a, pad["members"], P, p_lo, p_hi)
        if unpaired:
            # keep only the repeats we could actually account for, so the span
            # still lands on whole frames
            pad["members"] = [m for m in pad["members"] if m in prs]
            r["why"].append(f"{len(unpaired)} repeat(s) had no doubled step to pair "
                            f"with and are left as ordinary frames")
        r["doubles"] = sorted(prs.values())
        r["unpaired"] = unpaired
        r["exact"] = not unpaired
    elif r["klass"] == "GRID":
        # the whole region: on an uneven grid every frame is in the wrong place,
        # which is the same situation as a rate conversion
        r["advance"] = _grid_advance(a, lo, hi, grid["m"])
        r["defect"] = (lo, hi + 1)
    else:
        r["defect"] = (lo, hi + 1)
    return r


# ------------------------------------------------------------------ verdict

ALREADY = ("_rebuilt", "_seamfix", "_clean_", "_cadence", "_fixed", "_apo8",
           "_apo-8", "_ganim", "_chr-", "_repaired", "_even")

# the classes that earn a repair. Everything else is left exactly as it is.
REPAIRABLE = ("PAD", "SEAM", "PULLDOWN", "GRID")


class Verdict:
    def __init__(self, **kw):
        self.__dict__.update(kw)

    def as_dict(self):
        d = dict(self.__dict__)
        d.pop("analysis", None)
        return d


def diagnose(path, force=False, assume_fps=24.0, force_class=None):
    con = probe(path)
    base = os.path.basename(path)
    low = base.lower()

    refuse = None
    marks = [k for k in ALREADY if k in low]
    if marks and not force:
        refuse = ("already-processed",
                  f"the name contains {marks[0]!r}, so this file has been through a repair "
                  f"or an upscale already. These tools read the ORIGINAL defect to work out "
                  f"what happened; a previous pass has removed that evidence. Work from the "
                  f"raw download. --force overrides.")

    tag_hits = [t for t in ("apo-", "ganim", "chr-", "prob-", "iris-", "thm-")
                if t in con.tags.lower()]

    # A Topaz tag used to be a printed warning and nothing more. That was not
    # enough. "Use the raw download" is the most load-bearing rule in here - the
    # repair works by reading the ORIGINAL defect, and an enhance pass has both
    # erased that evidence and invented pixels of its own. A warning that scrolls
    # past in a window nobody is watching is not a rule, it is a hope. It also
    # fails in a way that looks like success: the motion comes out even, the
    # numbers look fine, and the picture carries a repaint that no longer matches
    # the grain around it.
    #
    # So it refuses, like the filename markers do, and --force still overrides for
    # anyone who knows what they are doing.
    if tag_hits and refuse is None and not force:
        refuse = ("enhanced-already",
                  f"this file carries an enhancement tag ({', '.join(tag_hits)}), so it has "
                  f"been through Topaz or a similar pass already - it is not a raw download. "
                  f"These tools read the ORIGINAL defect to work out what happened, and that "
                  f"pass has erased it and added pixels of its own. Repair the raw file, THEN "
                  f"enhance. --force overrides.")

    if con.fps and abs(con.fps - assume_fps) > 0.15 and refuse is None:
        refuse = ("not-24fps",
                  f"this file is {con.fps:.3f} fps, not {assume_fps:g}. Every measurement and "
                  f"every repair here assumes {assume_fps:g} fps, and on a {con.fps:.3f} fps "
                  f"clip they would be timed wrong. Convert it to {assume_fps:g} fps first, "
                  f"or run with --fps {con.fps:.3f} if you know that is right.")

    if con.vfr and refuse is None:
        refuse = ("variable-frame-rate",
                  "the frame timestamps in this file are not evenly spaced. That stutter is "
                  "in the container, not in the pictures - no interpolator can fix it. "
                  "Re-mux to constant frame rate first: "
                  "ffmpeg -i IN -c copy -video_track_timescale 24000 OUT, or re-encode with "
                  "-vsync cfr -r 24.")

    a = measure(path, con=con)
    segment(a)

    regions = []
    for seg in a.segments:
        for lo, hi in seg["active"]:
            regions.append(classify_region(a, lo, hi))

    if force_class:
        # You have overruled the diagnosis. Every region long enough to carry
        # the defect is re-labelled and the matching repair is planned from the
        # evidence already measured.
        for r in regions:
            if r["span"] >= 10 and r["klass"] != force_class:
                r["klass"] = force_class
                r["why"].append(f"forced to {force_class} on the command line")
                if force_class == "PAD" and r["pad"]["members"]:
                    P = r["pad"]["period"]
                    p_lo = max(r["lo"], min(r["pad"]["members"]) - P)
                    p_hi = min(r["hi"], max(r["pad"]["members"]) + P)
                    r["defect"] = (p_lo, p_hi + 1)
                    prs, unp = _pad_doubles(a, r["pad"]["members"], P, p_lo, p_hi)
                    r["pad"]["members"] = [m for m in r["pad"]["members"] if m in prs]
                    r["doubles"] = sorted(prs.values())
                elif force_class == "PULLDOWN" and r["spikes"]:
                    idx = [s["i"] for s in r["spikes"]]
                    pad_out = max(2, (r["cycle"] or 6) // 3)
                    r["defect"] = (max(r["lo"], min(idx) - pad_out),
                                   min(r["hi"], max(idx) + pad_out) + 1)
                elif force_class == "GRID":
                    m = (r.get("grid") or {}).get("m") or 2
                    r["advance"] = _grid_advance(a, r["lo"], r["hi"], m)
                    r["defect"] = (r["lo"], r["hi"] + 1)
                r["solid"] = True

    holds = [h for seg in a.segments for h in seg["holds"]]
    held_steps = sum(y - x + 1 for x, y in holds)

    # --- content guard -------------------------------------------------------
    # No padding anywhere, yet a doubled step every few frames. Real footage does
    # not look like that; a clip that was repaired once and never retimed does.
    total_spikes = sum(len(r["spikes"]) for r in regions)
    any_pad = any(r["klass"] == "PAD" for r in regions)

    # Before accusing a file of having been repaired already, check whether the
    # doubled steps sit on an exact short beat.
    #
    # This distinction is the whole of the guard. A clip that was repaired once
    # and never retimed has its doubled steps scattered - that is what made them
    # suspicious in the first place. A doubled step every fourth frame, exactly,
    # is not scatter: it is the OTHER half of padding, and it means the repeats
    # are there and were missed. Refusing then tells the user their original
    # download is second-hand goods, which is both wrong and impossible to argue
    # with. So a tight beat in the doubles cancels the accusation.
    tight_beat = None
    allspikes = sorted(s["i"] for r in regions for s in r["spikes"])
    if len(allspikes) >= 6:
        gaps = [b - x for x, b in zip(allspikes, allspikes[1:])]
        for P in (2, 3, 4, 5):
            if sum(1 for g in gaps if g == P) >= 0.7 * len(gaps):
                tight_beat = P
                break

    if (refuse is None and not force and not any_pad and total_spikes > a.n / 9
            and not tight_beat
            and not any(r["klass"] == "PULLDOWN" for r in regions)):
        refuse = ("looks-pre-repaired",
                  f"no padded repeats anywhere, yet a doubled step every "
                  f"{a.n // max(total_spikes, 1)} frames, and they are not on a regular "
                  f"beat. That is the fingerprint of a clip that has already been repaired "
                  f"once and never retimed. Work from the original download. "
                  f"--force overrides.")

    # whole-clip class = the class covering the most frames, but the repair is
    # still applied region by region, so a clean stretch is left clean.
    weight = {}
    for r in regions:
        weight[r["klass"]] = weight.get(r["klass"], 0) + r["span"]
    if not regions:
        overall = "STATIC"
    else:
        overall = max(weight.items(), key=lambda kv: kv[1])[0]
    if overall == "CLEAN" and held_steps > 0.6 * a.ns:
        overall = "STATIC"

    # --- a bad region is not outvoted by good ones ---------------------------
    #
    # Added in 3.1. The clip verdict was the class covering the most frames, and
    # nothing else. On a clip split into three regions where the first was
    # padded and the other two were clean, the whole-clip verdict came back
    # CLEAN and the repair did nothing at all - while the log row underneath it
    # read regions=PAD|CLEAN|CLEAN, rep=8, cov=1.00. The engine had found the
    # fault, written it down, and then outvoted itself.
    #
    # One region with real evidence of a repairable fault makes the clip
    # repairable. It costs nothing to be right about this: the repair has always
    # been planned region by region, so the clean stretches are copied through
    # untouched either way. All that was ever missing was permission to start.
    if overall not in REPAIRABLE:
        hurt = [r for r in regions if r["klass"] in REPAIRABLE and r.get("solid")]
        if hurt:
            big = max(hurt, key=lambda r: r["span"])
            overall = big["klass"]
            big["why"].append(
                f"the rest of the clip is fine; this region is repaired and "
                f"everything outside it is copied untouched")

    return Verdict(
        path=path, name=base, version=VERSION,
        ok=refuse is None,
        refuse_kind=refuse[0] if refuse else None,
        refuse_why=refuse[1] if refuse else None,
        fps=con.fps, frames=a.n, duration=(a.n / (con.fps or assume_fps)),
        width=con.width, height=con.height, codec=con.codec,
        has_audio=con.has_audio, topaz_tags=tag_hits, vfr=con.vfr,
        cuts=a.cuts, holds=holds, held_steps=held_steps,
        regions=regions, klass=overall, tight_beat=tight_beat,
        analysis=a,
    )


# ------------------------------------------------------------------ 5. plan

def _paired_repeat(a, s, lo, hi, reach=2):
    """The wasted slot belonging to an isolated doubled step, if there is one.

    A held frame and a dropped frame look the same from a distance and are not
    the same thing at all. When a generator freezes a frame it has to lose a
    real one to keep the count, so the damage comes in a PAIR: a repeat sitting
    next to a step that carries two frames of motion. When it simply drops a
    frame, there is no repeat anywhere near - the step is doubled and that is
    all.

    Telling them apart is what makes the repair work. The pair has a slot that
    can be painted into, so one frame is repainted and the freeze is gone. The
    lone drop has no spare slot, so the best that can be done is to spread the
    lurch over a short window - which is a smaller win, and it must not be
    mistaken for the bigger one.

    Added in 3.1, because the SEAM repair had been treating every hitch as the
    second kind. It found K2's held frame at 70 of 241 unaided, repainted 57
    frames around it, and left the held frame sitting exactly where it was.
    """
    for d in range(1, reach + 1):
        for j in (s - d, s + d):
            if lo <= j <= hi and a.dup[j]:
                return j
    return None


def plan(v, seam_window=4):
    """Turn the verdict into target positions on a motion timeline.

    Two arrays, both one entry per delivered frame:

        pos[i]     where frame i actually sits in motion time. A repeat does not
                   advance it. A missing frame advances it by two.
        target[i]  where frame i SHOULD sit for the motion to be even.

    Every class produces these same two arrays, so there is exactly one renderer
    and one thing to verify. And because every correction is written as a span
    whose two ENDS are pinned to real frames, the frame count, the frame rate and
    the duration cannot change - which is the rule this whole folder is built on.

    The span of the correction is chosen to match how far the defect actually
    reaches. That is the difference between fixing a clip and re-rendering it:

        PAD        the whole region, but positions land on whole numbers, so the
                   real frames are copied untouched and only the wasted slots
                   are painted.
        SEAM       a short window either side of each hitch. Nothing else in the
                   clip is touched at all.
        PULLDOWN   the whole region, because frames were thrown away all through
                   it and every frame is therefore in the wrong place.
    """
    a = v.analysis
    n = v.frames
    pos = [0.0] * n
    corrections = []

    # 1. base positions: walk the clip, advancing motion time step by step.
    #
    # Only a PADDED repeat fails to advance the clock - there, a real frame was
    # destroyed and replaced with a copy, so no new moment arrived. A frame in a
    # genuinely held pose is a real frame that happens to look like its
    # predecessor: time passed, nothing moved, and it advances by one like any
    # other. Treating those two the same is what used to drag held passages into
    # the repair and paint invented motion into a deliberate freeze.
    spike_at, lost, grid_adv, paired = set(), set(), {}, {}
    for r in v.regions:
        if r["klass"] == "SEAM":
            for s in r["spikes"]:
                spike_at.add(s["i"])
                j = _paired_repeat(a, s["i"], r["lo"], r["hi"])
                if j is not None and j not in lost:
                    # a held frame: the repeat is a wasted slot exactly like a
                    # padded one, and is repainted in place. One frame, not a
                    # window - and the freeze actually goes.
                    lost.add(j)
                    paired[s["i"]] = j
        elif r["klass"] == "PULLDOWN":
            for s in r["spikes"]:
                spike_at.add(s["i"])
        elif r["klass"] == "GRID":
            grid_adv.update(r.get("advance") or {})
        elif r["klass"] == "PAD":
            # the paired partners, not the loose spike list: one restored moment
            # per repeat, so the span comes out to a whole number of frames and
            # every real image is copied rather than repainted
            spike_at.update(r.get("doubles", []))
            lost.update(r["pad"]["members"])
    for i in range(a.ns):
        if i in lost:
            adv = 0.0
        elif i in spike_at:
            adv = 2.0
        elif i in grid_adv:
            adv = grid_adv[i]
        else:
            adv = 1.0
        pos[i + 1] = pos[i] + adv

    target = list(pos)

    # 2. correction spans
    for r in v.regions:
        d_lo, d_hi = r["defect"]                   # frames, inclusive..inclusive
        if r["klass"] == "PAD":
            corrections.append((d_lo, d_hi, "PAD", None))
        elif r["klass"] == "PULLDOWN":
            # Use the SNAPPED rate, not the count of what we spotted. A rate
            # conversion damages every frame in the stretch, so the honest model
            # is "this stretch holds (d_hi-d_lo) * R/24 frames of motion",
            # whether or not we found every last drop.
            motion = (d_hi - d_lo) * (r.get("snap_rate") or 24.0) / 24.0
            corrections.append((d_lo, d_hi, "PULLDOWN", motion))
        elif r["klass"] == "GRID":
            corrections.append((d_lo, d_hi, "GRID", None))
        elif r["klass"] == "SEAM":
            for s in r["spikes"]:
                j = paired.get(s["i"])
                if j is not None:
                    # a held frame and its doubled partner. The span only has to
                    # reach from the real frame before the pair to the real
                    # frame after it, which is three or four frames - and of
                    # those, exactly one is not a real picture and gets painted.
                    lo = max(r["lo"], min(j, s["i"]))
                    hi = min(r["hi"] + 1, max(j, s["i"]) + 2)
                    while hi > lo and (hi - 1) in lost:
                        hi -= 1
                    while lo < hi and (lo - 1) in lost:
                        lo += 1
                else:
                    # nothing was repeated, so there is no slot to paint into
                    # and the frame is simply gone. The lurch is spread over a
                    # short window instead. That is a smaller repair and it is
                    # honest about being one.
                    lo = max(r["lo"], s["i"] - seam_window)
                    hi = min(r["hi"] + 1, s["i"] + 1 + seam_window)
                if hi - lo >= 2 and (lo - 1) not in lost and (hi - 1) not in lost:
                    corrections.append((lo, hi, "SEAM", None))

    # merge overlapping spans so a frame is only ever corrected once
    corrections.sort()
    merged = []
    for lo, hi, kind, motion in corrections:
        if merged and lo <= merged[-1][1]:
            p = merged[-1]
            merged[-1] = (p[0], max(p[1], hi), p[2], None if p[3] is None or motion is None
                          else p[3] + motion)
        else:
            merged.append((lo, hi, kind, motion))

    for lo, hi, kind, motion in merged:
        if hi <= lo:
            continue
        span = motion if motion else (pos[hi] - pos[lo])
        k = hi - lo
        if kind == "GRID" and abs(span - k) > 1e-6:
            # a grid fault moves frames about inside the region; it never adds
            # or removes motion. If the arithmetic has drifted, the frame grid
            # is right and the arithmetic is wrong.
            span = float(k)
        if kind == "PAD" and abs(span - k) > 1e-6:
            # Padding restores to a whole number of frames or it is not padding.
            # If the arithmetic has not closed, snap to the 24 fps grid rather
            # than letting a fractional ramp repaint the entire stretch.
            span = float(k)
        for t in range(k + 1):
            target[lo + t] = pos[lo] + span * t / k
        # everything after a corrected span shifts with it, so the clip stays
        # one continuous timeline rather than a set of islands
        shift = (pos[lo] + span) - pos[hi]
        if abs(shift) > 1e-9:
            for j in range(hi + 1, n):
                pos[j] += shift
                target[j] += shift

    # 3. Which frames are real pictures we can draw from? All of them, except
    #    the padded repeats - those are copies standing in for a frame that no
    #    longer exists, and drawing from one would just re-draw the stutter.
    real = [0] + [i + 1 for i in range(a.ns) if i not in lost]
    real = sorted(set(real))
    return dict(pos=pos, target=target, real=real, spans=merged, lost=sorted(lost),
                touched=sum(1 for i in range(n) if abs(target[i] - pos[i]) > 1e-6))


def _detail(buf, w, h, step=2):
    """Mean |laplacian| - how much fine structure a frame carries."""
    tot = 0
    n = 0
    for y in range(2, h - 2, step):
        row = y * w
        for x in range(2, w - 2, step):
            i = row + x
            tot += abs(4 * buf[i] - buf[i - 1] - buf[i + 1] - buf[i - w] - buf[i + w])
            n += 1
    return tot / n if n else 0.0


def _ordinal(n):
    """2 -> '2nd', not '2th'. A one-in-two clip is a common case and it looked silly."""
    try:
        n = int(n)
    except Exception:
        return str(n)
    if 10 <= n % 100 <= 20:
        return f"{n}th"
    return f"{n}{ {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th') }".replace(" ", "")


def _corr(xs, ys):
    n = len(xs)
    if n < 8:
        return None
    mx = sum(xs) / n
    my = sum(ys) / n
    sxy = sxx = syy = 0.0
    for a, b in zip(xs, ys):
        da = a - mx
        db = b - my
        sxy += da * db
        sxx += da * da
        syy += db * db
    if sxx <= 0 or syy <= 0:
        return None
    return sxy / math.sqrt(sxx * syy)


def ghosting(path, repainted, width=640, step=3):
    """Are the repainted frames double images rather than invented ones?

    An invented frame G sits between two real ones, A and B. Look at the two
    halves of the change across it and ask how alike they are:

        corr(G - A,  B - G)

    A straight average - G = (A+B)/2 - makes those two halves the SAME picture,
    so this comes out at exactly 1.0 whatever the footage. Real motion between A
    and B makes them differ, and on untouched material the figure sits near zero
    or below.

    This needs its own measurement because a detail score cannot see it: a
    doubled edge counts as detail retained, so a blend can score WELL on
    sharpness while being the single most objectionable thing this tool can
    produce. On fast action that is not blur, it is two pictures on top of each
    other, and the eye reads it as a stutter of its own.

    Returns (painted_median, real_median, n_painted). Compare the two: painted
    should sit near real. Far above it means averaging.
    """
    con = probe(path)
    if not repainted or not con.width or not con.height:
        return None
    rp = sorted(set(int(i) for i in repainted))
    total = con.nb_frames or 0

    need = set()
    for i in rp:
        need.update((i - 1, i, i + 1))
    # a baseline from this clip's own untouched frames, so the number is
    # calibrated to the footage rather than to a constant someone chose
    base_mid = []
    i = 2
    while i < (total - 2 if total else 0) and len(base_mid) < 20:
        if not any(k in set(rp) for k in (i - 1, i, i + 1)):
            base_mid.append(i)
            need.update((i - 1, i, i + 1))
            i += 4
        else:
            i += 1
    if total:
        need = {k for k in need if 0 <= k < total}
    if len(need) < 6:
        return None

    W = width
    H = int(round(con.height * W / con.width / 2)) * 2
    if H < 8:
        return None
    size = W * H
    keep = {}
    try:
        proc = subprocess.Popen(
            ["ffmpeg", "-v", "error", "-i", path,
             "-vf", f"scale={W}:{H},format=gray", "-f", "rawvideo", "-"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    except Exception:
        return None
    idx = 0
    try:
        while True:
            buf = proc.stdout.read(size)
            if len(buf) < size:
                break
            if idx in need:
                keep[idx] = buf
            idx += 1
    except Exception:
        return None
    finally:
        try:
            proc.stdout.close()
        except Exception:
            pass
        try:
            proc.wait(timeout=10)
        except Exception:
            proc.kill()

    def score(i):
        A, G, B = keep.get(i - 1), keep.get(i), keep.get(i + 1)
        if not (A and G and B):
            return None
        xs = [G[k] - A[k] for k in range(0, size, step)]
        ys = [B[k] - G[k] for k in range(0, size, step)]
        return _corr(xs, ys)

    painted = [x for x in (score(i) for i in rp) if x is not None]
    plain = [x for x in (score(i) for i in base_mid) if x is not None]
    if len(painted) < 3 or len(plain) < 3:
        return None
    return (statistics.median(painted), statistics.median(plain), len(painted))


def texture(path, repainted, width=960):
    """Do the repainted frames carry the same fine detail as the frames beside them?

    Everything else in this file measures MOTION. That is not the whole story, and
    a clip can pass every one of those tests and still look wrong.

    An invented frame is built out of two real ones, and averaging costs
    high-frequency detail - fine texture, film grain, the inside of foliage. So a
    repainted frame can sit in exactly the right place and still be softer than
    its neighbours. On a padded clip the repaints land every fourth frame, which
    means that softness arrives on a regular beat; and a texture that pulses six
    times a second is far more visible than one that is uniformly soft. Measured
    on a real clip: repainted frames read 0.90-0.94 against neighbours at 1.00,
    and it was described as "not as smooth" by someone who could see it plainly
    and could not name it.

    The cure is not a better interpolator - it is a pass over EVERY frame
    afterwards, so that whatever the repaint did to those frames is done to all of
    them and nothing stands out. That is why cadence-then-Topaz looks better than
    Topaz-then-cadence, and this is the measurement that says so out loud.

    Returns (ratio, n_repainted, n_neighbours), ratio being the median detail of
    the repainted frames over the median of their untouched immediate neighbours.
    1.0 is a match. Below about 0.95 is worth acting on. None if it cannot tell.
    """
    con = probe(path)
    if not repainted or not con.width or not con.height:
        return None
    rp = set(int(i) for i in repainted)
    need = set()
    for i in rp:
        need.update((i - 1, i, i + 1))
    total = con.nb_frames or 0
    if total:
        need = {i for i in need if 0 <= i < total}
    if len(need) < 6:
        return None

    W = width
    H = int(round(con.height * W / con.width / 2)) * 2
    if H < 8:
        return None
    size = W * H
    painted, plain = [], []
    try:
        proc = subprocess.Popen(
            ["ffmpeg", "-v", "error", "-i", path,
             "-vf", f"scale={W}:{H},format=gray", "-f", "rawvideo", "-"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    except Exception:
        return None
    idx = 0
    try:
        while True:
            buf = proc.stdout.read(size)
            if len(buf) < size:
                break
            if idx in need:
                d = _detail(buf, W, H)
                (painted if idx in rp else plain).append(d)
            idx += 1
    except Exception:
        return None
    finally:
        try:
            proc.stdout.close()
        except Exception:
            pass
        try:
            proc.wait(timeout=10)
        except Exception:
            proc.kill()

    if len(painted) < 3 or len(plain) < 3:
        return None
    a = statistics.median(painted)
    b = statistics.median(plain)
    if b <= 0:
        return None
    return (a / b, len(painted), len(plain))


def evenness(path, a=None):
    """How uneven the motion is, as two numbers the eye would agree with.

        spread  the 90th-percentile deviation of a step from its local level.
                A clip with no cadence problem sits around 0.05. A padded clip
                is well above 1.0, because every fourth step is near zero and
                its partner is near double.
        worst   the third-largest step relative to its neighbours - the hitch
                you actually notice. Third-largest and not largest, because a
                clip that starts on a held pose and then moves has one honest
                jump at the moment it starts moving, and that one jump should
                not stand for the whole clip.

    Held passages are excluded: a deliberate freeze is not unevenness, and
    counting it would hide whether the repair did anything.

    This is what the repair uses to check its own work. A repair that does not
    move these numbers has not done anything, and says so.
    """
    a = a or measure(path)
    in_hold = [False] * a.ns
    for x, y in _runs(a.dup):
        if y > x:
            for i in range(x, y + 1):
                in_hold[i] = True
    skip = set(a.cuts)

    # Judge each step against its IMMEDIATE neighbours, not against a wide
    # median. A shot that accelerates or slows down is not uneven - every step
    # differs a little from the last, smoothly, and a wide window scores that as
    # a fault. A stutter is a step that disagrees with the two either side of it,
    # which is exactly what the eye picks up.
    # The first and last couple of steps are skipped. A frame at the very edge
    # of a clip has nothing beyond it to interpolate from, so no repair can
    # touch it; letting it set the score would make every repair look like a
    # failure for a frame nobody could have fixed.
    vals, tops = [], []
    for i in range(1, max(1, a.ns - 2)):
        if in_hold[i] or a.flash[i] or i in skip:
            continue
        ne = [a.step[j] for j in (i - 3, i - 2, i - 1, i + 1, i + 2, i + 3)
              if 0 <= j < a.ns and not in_hold[j] and not a.flash[j] and j not in skip]
        if len(ne) < 3:
            continue
        loc = statistics.median(ne)
        if loc <= 0:
            continue
        rr = a.step[i] / loc
        vals.append(abs(rr - 1.0))
        tops.append(rr)
    if len(vals) < 8:
        return None
    s = sorted(vals)
    t = sorted(tops, reverse=True)
    worst = t[2] if len(t) >= 40 else t[0]
    return (round(s[int(len(s) * 0.90)], 3), round(worst, 2))


# ------------------------------------------------------------- 6. reporting
#
# Both buttons print from these two functions. That is the point: the thing that
# tells you what is wrong and the thing that repairs it are reading the same
# verdict out of the same engine, so they cannot tell you two different stories
# about the same clip.

CLASS_TEXT = {
    "CLEAN": "motion is already even",
    "STATIC": "a held pose - the scene genuinely stops",
    "PAD": "padded repeats - every Nth frame is a copy of the one before it",
    "SEAM": "isolated missing frames - a hitch every second or so",
    "PULLDOWN": "frames thrown away throughout, to force a faster clip into 24 fps",
    "GRID": "no frame repeated, but the steps alternate long/short on a strict beat",
    "IRREGULAR": "big steps, but they are bursts of fast motion, not a defect",
    "AMBIGUOUS": "two explanations fit about equally well",
}


def wrap(text, width=74):
    words, line, out = text.split(), "", []
    for w in words:
        if len(line) + len(w) + 1 > width:
            out.append(line); line = w
        else:
            line = (line + " " + w).strip()
    if line:
        out.append(line)
    return out


def describe(v):
    print(f"{v.name}")
    print(f"  {v.frames} frames, {(v.fps or 0):g} fps, {v.duration:.3f}s, "
          f"{v.width}x{v.height}, audio: {'yes' if v.has_audio else 'no'}")
    if v.topaz_tags:
        print(f"  carries Topaz tags {v.topaz_tags} - this is not a raw download")
    if v.cuts:
        print(f"  {len(v.cuts)} hard cut(s) inside the clip, at frame {v.cuts} - "
              f"nothing will be interpolated across them")
    if v.holds:
        print(f"  {len(v.holds)} held passage(s), {v.held_steps} frames - "
              f"kept exactly as they are")
    print()
    if not v.ok:
        print(f"  REFUSED ({v.refuse_kind})")
        for line in wrap(v.refuse_why):
            print("    " + line)
        return
    print(f"  DIAGNOSIS: {v.klass}  -  {CLASS_TEXT.get(v.klass, '')}")
    for r in v.regions:
        if r["klass"] == "CLEAN" and len(v.regions) > 1:
            print(f"    frames {r['lo']}-{r['hi'] + 1}: clean, left alone")
            continue
        line = f"    frames {r['lo']}-{r['hi'] + 1}: {r['klass']}"
        if r["klass"] == "PAD":
            line += (f" - every {_ordinal(r['pad']['period'])} frame is a repeat, "
                     f"{len(r['pad']['members'])} of them, "
                     f"beat {r['pad']['cover']:.0%} solid")
        elif r["klass"] == "PULLDOWN":
            line += f" - {len(r['spikes'])} frames dropped"
            if r.get("cycle"):
                line += (f", the pattern repeating every {r['cycle']} frames "
                         f"({r['cycle_fit']:.0%} of the time)")
            line += (f"; that is a {r['implied_rate']:g} fps source"
                     f" -> rebuilt as {r['snap_rate']:g} fps of motion")
        elif r["klass"] == "SEAM":
            line += (f" - {len(r['spikes'])} isolated missing frames, before frames "
                     f"{[s['i'] + 1 for s in r['spikes']][:10]}")
        elif r["klass"] == "GRID":
            g = r.get("grid") or {}
            line += (f" - no frame is repeated, but every {g.get('m')} steps the "
                     f"motion repeats a long/short pattern, {g.get('amp', 0):.0%} "
                     f"apart (F={g.get('F')})")
        elif r["klass"] == "IRREGULAR":
            line += f" - {len(r['all_candidates'])} big steps"
            if r["ramp_fraction"] >= 0.4:
                line += (f", but {r['ramp_fraction']:.0%} of them ramp up and down with "
                         f"their neighbours; that is fast motion, not a defect")
            else:
                line += (f", too few or too weak to call a defect "
                         f"({len(r['spikes'])} stand alone)")
        elif r["klass"] == "AMBIGUOUS":
            line += " - cannot tell which; see the scores"
        print(line)
        if r["scores"]:
            print("      evidence: " + ", ".join(
                f"{k} {vv:.2f}" for k, vv in sorted(r["scores"].items(),
                                                    key=lambda kv: -kv[1])))
        # 3.1: these were being collected and never shown. They are the engine's
        # own reasoning about a close call, which is exactly what you want in
        # front of you when a clip comes out wrong.
        for note in r.get("why", []):
            for k, ln in enumerate(wrap(note, 66)):
                print(("      note: " if k == 0 else "            ") + ln)


def plan_summary(v, pl, n_repaint, fps=24.0, edge=()):
    print(f"  PLAN")
    for lo, hi, kind, _m in pl["spans"]:
        print(f"    even out frames {lo}-{hi} ({kind})")
    n = v.frames
    print(f"    {n_repaint} of {n} frames repainted, {n - n_repaint} copied untouched")
    if edge:
        print(f"    frame {list(edge)[0]} is at the very end with nothing after it to "
              f"draw from; left as the copy it already is")
    print(f"    -> {n} frames, {fps:g} fps, {n / fps:.3f}s  "
          f"(identical to the source, by construction)")
    if v.has_audio:
        print(f"    original audio carried across and still in sync")


# ------------------------------------------------------------- 7. the log
#
# One line per clip, appended beside the scripts. This is not the tool learning
# anything - it cannot, it is a program. It is the evidence trail, so that when
# a clip does come out wrong, the numbers that produced the wrong answer are
# already on disk instead of having to be re-derived from a description of what
# went wrong. Recalibrating against a hundred of your own clips is a different
# proposition from recalibrating against the four I happened to be shown.
#
# It never interrupts a run. If the log cannot be written, the repair carries on.

LOG_NAME = "cadence-log.tsv"

LOG_COLUMNS = [
    "when", "file", "action", "frames", "fps", "size", "audio",
    "verdict", "refused", "scores",
    "pad_period", "pad_repeats", "pad_cover", "unpaired",
    "spikes", "cycle", "cycle_fit", "implied_rate", "used_rate",
    "cuts", "held_frames", "regions",
    "repainted", "method", "spread_before", "worst_before",
    "spread_after", "worst_after", "outcome", "tool",
    # appended, so rows written by older versions still line up under their
    # own header - they simply stop one column short
    "texture",
    # 3.1
    "grid_m", "grid_amp", "grid_F", "evidence",
]


def _cell(x):
    s = "" if x is None else str(x)
    return s.replace("\t", " ").replace("\n", " ").replace("\r", " ")


def log_run(v, action, repainted=None, method=None, before=None, after=None,
            outcome=None, folder=None, texture=None):
    try:
        import datetime
        folder = folder or os.path.dirname(os.path.abspath(__file__))
        path = os.path.join(folder, LOG_NAME)
        big = max(v.regions, key=lambda r: r["span"]) if v.regions else {}
        row = {
            "when": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
            "file": os.path.basename(v.path),
            "action": action,
            "frames": v.frames, "fps": f"{(v.fps or 0):g}",
            "size": f"{v.width}x{v.height}", "audio": "y" if v.has_audio else "n",
            "verdict": v.klass if v.ok else "REFUSED",
            "refused": v.refuse_kind or "",
            "scores": " ".join(f"{k}={vv:.2f}" for k, vv in
                               sorted(big.get("scores", {}).items(), key=lambda kv: -kv[1])),
            "pad_period": (big.get("pad") or {}).get("period"),
            "pad_repeats": len((big.get("pad") or {}).get("members", [])),
            "pad_cover": f"{(big.get('pad') or {}).get('cover', 0):.2f}",
            "unpaired": len(big.get("unpaired", []) or []),
            "spikes": len(big.get("spikes", []) or []),
            "cycle": big.get("cycle"),
            "cycle_fit": big.get("cycle_fit"),
            "implied_rate": big.get("implied_rate"),
            "used_rate": big.get("snap_rate"),
            "cuts": len(v.cuts), "held_frames": v.held_steps,
            "regions": "|".join(r["klass"] for r in v.regions),
            "repainted": repainted,
            "method": method or "",
            "spread_before": before[0] if before else None,
            "worst_before": before[1] if before else None,
            "spread_after": after[0] if after else None,
            "worst_after": after[1] if after else None,
            "outcome": outcome or "",
            "tool": VERSION,
            "texture": (f"{texture[0]:.3f}" if texture else ""),
            "grid_m": (big.get("grid") or {}).get("m"),
            "grid_amp": f"{(big.get('grid') or {}).get('amp', 0):.3f}",
            "grid_F": (big.get("grid") or {}).get("F"),
            "evidence": "|".join(k for k, ok in (big.get("evidence") or {}).items() if ok),
        }
        new = not os.path.exists(path)
        head = "\t".join(LOG_COLUMNS)
        if not new:
            # When a column is added, an existing log keeps its old header and
            # every new row then runs off the end of it. Because columns are only
            # ever APPENDED, the old header is a prefix of the new one, so the fix
            # is to widen the header in place: earlier rows are simply short, and
            # a short row under a wider header reads as empty cells, which is
            # exactly what those runs measured - nothing.
            try:
                with open(path, "r", encoding="utf-8") as f:
                    lines = f.readlines()
                if lines and lines[0].rstrip("\n") != head:
                    old_cols = lines[0].rstrip("\n").split("\t")
                    if old_cols == LOG_COLUMNS[:len(old_cols)]:
                        lines[0] = head + "\n"
                        with open(path, "w", encoding="utf-8") as f:
                            f.writelines(lines)
            except Exception:
                pass
        with open(path, "a", encoding="utf-8") as f:
            if new:
                f.write(head + "\n")
            f.write("\t".join(_cell(row.get(c)) for c in LOG_COLUMNS) + "\n")
        return path
    except Exception:
        return None


if __name__ == "__main__":
    for p in sys.argv[1:]:
        v = diagnose(p)
        print(json.dumps(v.as_dict(), indent=2, default=str))
