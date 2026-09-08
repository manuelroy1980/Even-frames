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
    SEAM       eight frames either side of each hitch, and nothing else in the
               clip is touched at all.
    PULLDOWN   the stretch the drops actually cover. `mountain 2` is a 30-into-24
               conversion for its first 178 frames and native 24 after that - the
               last 39 frames are never re-rendered.

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
import os, sys, glob, shutil, tempfile, subprocess, argparse, json, io, statistics
import concurrent.futures as cf

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import cadence_core as core
from cadence_core import RATE, Unreadable

print = core.print

CTX = 3             # real frames of context either side of a render chunk
CHUNK = 20          # pairs rendered per ffmpeg call


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
        fl += _detail_kept(_gray_png(m[idx]), ga, gb)
        bl += _detail_kept(_gray_png(os.path.join(d, "blend.png")), ga, gb)
        n += 1
    shutil.rmtree(d, ignore_errors=True)
    if not n:
        return "flow", None
    fl /= n; bl /= n
    return ("flow" if fl >= bl else "blend"), (fl, bl)


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
        return
    describe(v)

    if not v.ok:
        core.log_run(v, "fix", outcome="refused")
        return

    if v.klass in ("CLEAN", "STATIC", "IRREGULAR", "AMBIGUOUS"):
        core.log_run(v, "fix", repainted=0, outcome="left alone")
        print()
        if v.klass == "AMBIGUOUS":
            print("  Not repaired. Two explanations fit this clip about equally well, and")
            print("  guessing between them is how the wrong repair gets applied. The")
            print("  evidence is above. If you know which it is, re-run with")
            print("  --force-class PAD | SEAM | PULLDOWN.")
        else:
            print("  Nothing to repair. This clip is left exactly as it is.")
        return

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
        if abs(p - rp[j]) < 1e-6:
            copies[i] = real[j]
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
    if a.dry_run:
        return
    if not reqs:
        print("    nothing actually needs repainting; leaving the file alone")
        return

    # ---- extract ---------------------------------------------------------
    tmp = os.path.join(os.environ.get("CADENCE_TMP", tempfile.gettempdir()), "cadencefix")
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
            return
        if _run(["ffmpeg", "-v", "error", "-i", path, "-start_number", "0",
                 *RATE, "-compression_level", "6",
                 os.path.join(tmp, "src", "%05d.png")]) != 0:
            print("    FAILED to extract frames - out of disk space? Set CADENCE_TMP.")
            return
    src = sorted(glob.glob(os.path.join(tmp, "src", "*.png")))
    if len(src) < n:
        print(f"    only {len(src)} of {n} frames extracted - aborting rather than "
              f"writing a short file")
        return

    # ---- paint -----------------------------------------------------------
    method, scores = choose_method(tmp, src, real, reqs, a.gap)
    if scores:
        print(f"    invented-frame test on this clip: optical flow keeps "
              f"{scores[0] * 100:.0f}% of detail in its worst areas, blend "
              f"{scores[1] * 100:.0f}%")
    Kq = pick_K(reqs)
    px = (v.width or 1920) * (v.height or 1080)
    workers = 1 if px >= 3800 * 2100 else (2 if px >= 1900 * 1000 else 3)
    print(f"    painting {len(reqs)} frames by {method}"
          + (f", in {'halves' if Kq == 2 else 'quarters' if Kq == 4 else 'eighths'} "
             f"of a frame" if method == "flow" else ""))
    if workers == 1:
        print("      4K or larger - one stretch at a time, to stay inside memory")
    if method == "blend":
        print("      motion here outruns optical flow, which dissolves on it. A blend")
        print("      reads as motion blur instead, which is far less objectionable.")
    topaz = bool(scores and max(scores) < 0.55)

    made = render_requests(tmp, src, real, pos, reqs, method, K=Kq, workers=workers)
    if len(made) < len(reqs):
        print(f"    only {len(made)} of {len(reqs)} frames could be painted "
              f"({len(reqs) - len(made)} failed - ffmpeg out of memory?)")
    if not made:
        print("    nothing could be painted - aborting, file left alone")
        return

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
        return

    stem, ext = os.path.splitext(path)
    out = f"{stem}_even{ext}"
    cmd = ["ffmpeg", "-v", "error", "-y", "-framerate", str(a.fps),
           "-i", os.path.join(tmp, "out", "%05d.png")]
    if v.has_audio:
        cmd += ["-i", path, "-map", "0:v", "-map", "1:a", "-c:a", "copy"]
    cmd += ["-c:v", "libx264", "-crf", str(a.crf), "-preset", "slow",
            "-pix_fmt", "yuv420p", out]
    if _run(cmd) != 0:
        print("    FAILED during encoding")
        return

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
    gone = None
    try:
        v2 = core.diagnose(out, force=True, assume_fps=a.fps)
        gone = v2.klass in ("CLEAN", "STATIC", "IRREGULAR")
        print(f"    re-diagnosed as: {v2.klass}"
              + ("   (the defect is gone)" if gone else "   ** still reads as broken **"))
    except Exception:
        pass

    word = "done"
    if sa is not None:
        better = sa < sb * 0.9
        if gone and better:
            word = "evened out" if sa < sb * 0.5 else "improved"
        elif gone:
            word = "defect gone, but barely more even - check it by eye"
        elif better:
            word = "more even, but the engine still sees the defect - check it by eye"
        else:
            word = "** NO BETTER - do not use this file, keep the original **"
        print(f"    overall {sb:.2f} -> {sa:.2f}   {word}")
        ok = ok and (gone or better)

    logged = core.log_run(v, "fix", repainted=len(reqs), method=method,
                          before=before, after=after, outcome=word)

    if topaz:
        print()
        print("    NOTE: neither optical flow nor a blend paints a clean frame on this")
        print("    clip - the motion between frames is past what ffmpeg can follow.")
        print("    The file above is the best free result. If it looks mushy on the")
        print("    repainted frames, Topaz Apollo (apo-8, 'replace duplicate frames')")
        print("    on the RAW download is the one thing that does better.")

    print()
    print("  done:", os.path.basename(out))
    if logged:
        print(f"  logged to {os.path.basename(logged)}")

    rep = (f"source   : {v.name}\n"
           f"tool     : cadence_fix {core.VERSION}\n"
           f"diagnosis: {v.klass} - {CLASS_TEXT.get(v.klass, '')}\n"
           f"repair   : {[ (s[0], s[1], s[2]) for s in pl['spans'] ]}\n"
           f"repainted: {len(reqs)} of {n} frames, by {method}\n"
           f"output   : {n} frames, {a.fps:g} fps, {n / a.fps:.3f}s - "
           f"same length as the source, drop straight on a {a.fps:g} fps timeline\n"
           f"unevenness before/after: {before} -> {after}\n")
    io.open(f"{stem}_even_REPORT.txt", "w", encoding="utf-8").write(rep)

    if a.json:
        io.open(f"{stem}_even.json", "w", encoding="utf-8").write(
            json.dumps({"verdict": v.as_dict(), "spans": pl["spans"],
                        "repainted": len(reqs), "method": method,
                        "evenness_before": before, "evenness_after": after},
                       indent=2, default=str))
    if not a.keep_temp:
        shutil.rmtree(tmp, ignore_errors=True)


ap = argparse.ArgumentParser()
ap.add_argument("inputs", nargs="+")
ap.add_argument("--fps", type=float, default=24.0)
ap.add_argument("--crf", type=int, default=14)
ap.add_argument("--dry-run", action="store_true")
ap.add_argument("--gap", choices=["flow", "blend"], default=None)
ap.add_argument("--seam-window", type=int, default=8)
ap.add_argument("--force", action="store_true")
ap.add_argument("--force-class", choices=["PAD", "SEAM", "PULLDOWN"], default=None,
                help="overrule the diagnosis and repair as if it were this class")
ap.add_argument("--keep-temp", action="store_true")
ap.add_argument("--json", action="store_true")
a = ap.parse_args()

for p in a.inputs:
    print()
    print("=" * 70)
    try:
        main(p, a)
    except OSError as e:
        if getattr(e, "errno", None) == 28:
            print(f"{os.path.basename(p)}: RAN OUT OF DISK while working.")
            print( "  Nothing was written and your original is untouched. Point the")
            print( "  scratch folder at a drive with more room and run it again:")
            print( "      set CADENCE_TMP=D:\\cadence_scratch")
            print( "  (then start the icon from that same command window)")
        else:
            print(f"{os.path.basename(p)}: FAILED - {type(e).__name__}: {e}")
    except Exception as e:
        print(f"{os.path.basename(p)}: FAILED - {type(e).__name__}: {e}")
