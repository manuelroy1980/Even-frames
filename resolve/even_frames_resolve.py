#!/usr/bin/env python
"""Repair the clips on a Resolve timeline track and stack the results above them.

Why this is only a few hundred lines: the repair guarantees that the file it
writes has the SAME frame count, the SAME frame rate and the SAME duration as
the file it read. That makes a repaired file a drop-in replacement - the same
source in and out points land on the same pictures, so a repaired clip goes at
the identical record position on the track above with nothing to retime and
nothing to conform. Every hard part of "conform a fixed clip back into a cut"
was already paid for upstream.

It runs OUTSIDE Resolve on purpose. A repair takes minutes per clip, and run
from Resolve's own console that means a frozen application with no progress and
no way out. Run from a command window it prints as it goes and can be stopped.

    even_frames_resolve.py                     current timeline, V1 -> V2
    even_frames_resolve.py --track 2           read V2, write to V3
    even_frames_resolve.py --timeline "Timeline 24"
    even_frames_resolve.py --dry-run           diagnose and report, change nothing
    even_frames_resolve.py --only 1,4,7        just those clips, by the number shown

Nothing on the source track is ever modified. The originals stay exactly where
they are; the repairs go above them so you can switch the top track on and off
to compare.
"""
from __future__ import annotations
import argparse, os, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.dirname(HERE)
sys.path.insert(0, TOOLS)


# --------------------------------------------------------------------------
# connecting to Resolve
# --------------------------------------------------------------------------
def connect():
    """Return the Resolve object, or exit with something a human can act on."""
    try:
        import DaVinciResolveScript as bmd            # set up by the .bat
    except ImportError:
        sys.exit(
            "Could not load the Resolve scripting module.\n"
            "Start this from EVEN-FRAMES-IN-RESOLVE.bat, which points Python at it.\n"
            "If you are running it by hand, the three variables it sets are listed\n"
            "at the top of that file.")
    r = bmd.scriptapp("Resolve")
    if r is None:
        sys.exit("Resolve is not running, or scripting is switched off.\n"
                 "Open Resolve, then Preferences > System > General and set\n"
                 "'External scripting using' to Local.")
    return r


# --------------------------------------------------------------------------
# reading the timeline
# --------------------------------------------------------------------------
VIDEO_EXT = (".mp4", ".mov", ".mxf", ".avi", ".mkv", ".webm", ".m4v")


def _core():
    import cadence_core as core
    return core


def read_track(tl, track):
    """Every clip on one video track, with what we need to place it again."""
    out = []
    for it in (tl.GetItemListInTrack("video", track) or []):
        mpi = it.GetMediaPoolItem()
        path = mpi.GetClipProperty("File Path") if mpi else None
        out.append({
            "item": it,
            "name": it.GetName(),
            "path": path or "",
            "rec": it.GetStart(),                 # absolute timeline frame
            "dur": it.GetDuration(),
            "src_in": it.GetLeftOffset(),         # source frame the clip starts on
        })
    return out


def placeable(c):
    """Why this clip cannot be handled, or None if it can."""
    if not c["path"]:
        return "no source file (compound, Fusion, generator or still)"
    if not c["path"].lower().endswith(VIDEO_EXT):
        return "not a video file"
    if not os.path.exists(c["path"]):
        return "the source file is offline"
    return None


# --------------------------------------------------------------------------
# the repair
# --------------------------------------------------------------------------
def even_path(src):
    """The newest repair beside src, or None if there is none yet.

    Repairs are never written over, so a file can have several. The newest is
    the one to place; the older ones stay on disk to compare it against.
    """
    import cadence_core as core
    v = core.even_versions(src)
    return v[-1] if v else None


def graded_from_report(out):
    """True/False if the report beside the repair says it improved, None if unknown.

    Same arithmetic the repair uses on itself: spread carries a fault spread over
    every frame, worst carries a fault in three frames out of ninety, and one lone
    bad hitch is more visible than a slight wobble everywhere.
    """
    stem = os.path.splitext(out)[0]
    try:
        for line in open(f"{stem}_REPORT.txt", encoding="utf-8", errors="replace"):
            # A double image outranks every other number in the report. The
            # evenness figures on such a file look excellent - that is exactly
            # what makes it dangerous.
            if line.startswith("doubled") and "DO NOT USE" in line:
                return False
            if line.startswith("unevenness before/after:"):
                a, b = line.split(":", 1)[1].split("->")
                bs, bw = [float(x) for x in a.strip().strip("()").split(",")]
                as_, aw = [float(x) for x in b.strip().strip("()").split(",")]
                sb = bs + 0.5 * (bw - 1)
                sa = as_ + 0.5 * (aw - 1)
                return sa <= sb * 1.1
    except Exception:
        pass
    return None


def repair(src, crf, log):
    """Run the repair on one file. Returns (path, note) - path None if unusable.

    A repaired file that is already there and newer than its source is reused
    rather than rebuilt. Repairing a 1080p clip costs minutes; doing it twice
    because a script was run twice is the kind of waste that stops people using
    a tool at all.
    """
    before = set(_core().even_versions(src))
    out = even_path(src)
    if out and os.path.getmtime(out) >= os.path.getmtime(src):
        # A repair the tool graded NO BETTER still leaves its .mp4 on disk beside
        # the source. Reusing it on trust would quietly place the very file the
        # tool told you not to use - and the second run would look cleaner than
        # the first, which is the worst way to lose a warning. The report written
        # next to it carries the numbers, so re-apply the same test.
        verdict = graded_from_report(out)
        if verdict is False:
            return None, "a previous run graded this NO BETTER - not reusing it"
        return out, ("already repaired, reused" if verdict is not None
                     else "already repaired, reused (no report to check it against)")

    cmd = [sys.executable, os.path.join(TOOLS, "cadence_fix.py"), src, "--crf", str(crf)]
    # Stream it rather than capturing it. A 4K clip takes five or six minutes,
    # and a window that says "this is the slow part" and then prints nothing for
    # six minutes is indistinguishable from a window that has hung.
    log(f"      running the repair - its own output follows")
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            universal_newlines=True, bufsize=1)
    lines = []
    for line in proc.stdout:
        lines.append(line)
        line = line.rstrip()
        if line:
            log(f"      | {line}")
    proc.wait()
    text = "".join(lines)

    # The tool grades its own work. Believe it - that verdict exists precisely
    # so that nothing downstream has to guess whether a repair was worth using.
    if "NO BETTER" in text:
        return None, "the repair came out no better - original left in place"
    for phrase, why in (("Nothing to repair", "already even, nothing to do"),
                        ("left exactly as it is", "already even, nothing to do"),
                        ("REFUSED", "refused: already processed, or unreadable"),
                        ("AMBIGUOUS", "ambiguous diagnosis - the tool would not guess"),
                        ("NOT ENOUGH ROOM", "out of scratch space - set CADENCE_TMP")):
        if phrase in text:
            return None, why
    # The repair picks its own filename, because it will not write over one that
    # is already there. Find the file this run actually produced.
    made = [f for f in _core().even_versions(src) if f not in before]
    out = made[-1] if made else None
    if proc.returncode != 0 or not out:
        tail = [l for l in text.strip().splitlines() if l.strip()][-1:] or ["no output"]
        return None, f"the repair produced no file ({tail[0].strip()[:70]})"

    verdict = "repaired"
    for phrase in ("evened out", "improved", "check it by eye"):
        if phrase in text:
            verdict = f"repaired, {phrase}"
            break
    return out, verdict


def same_shape(a, b):
    """Frame count, rate and duration identical? The placement depends on it.

    The repair promises this and checks it itself, but the promise is what lets
    this script put a clip at a fixed position and walk away. Something that
    load-bearing gets checked on this side too.
    """
    import cadence_core as core
    p, q = core.probe(a), core.probe(b)
    if p.nb_frames and q.nb_frames and p.nb_frames != q.nb_frames:
        return f"frame count changed, {p.nb_frames} -> {q.nb_frames}"
    if p.duration and q.duration and abs(p.duration - q.duration) > 0.02:
        return f"duration changed, {p.duration:.3f}s -> {q.duration:.3f}s"
    if p.fps and q.fps and abs(p.fps - q.fps) > 0.01:
        return f"frame rate changed, {p.fps:g} -> {q.fps:g}"
    return None


# --------------------------------------------------------------------------
# putting it back
# --------------------------------------------------------------------------
def bin_named(mp, name):
    root = mp.GetRootFolder()
    for f in root.GetSubFolderList():
        if f.GetName() == name:
            return f
    return mp.AddSubFolder(root, name)


def track_is_clear(tl, track, spans):
    """Nothing already sitting where the repairs need to go."""
    busy = []
    for it in (tl.GetItemListInTrack("video", track) or []):
        a, b = it.GetStart(), it.GetEnd()
        for lo, hi in spans:
            if a < hi and lo < b:
                busy.append(it.GetName())
                break
    return busy


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--timeline", default=None, help="by name; default is the open one")
    ap.add_argument("--track", type=int, default=1, help="video track to read (default 1)")
    ap.add_argument("--crf", type=int, default=16, help="quality of the repaired file")
    ap.add_argument("--dry-run", action="store_true", help="report only, change nothing")
    ap.add_argument("--only", default=None, help="clip numbers to do, e.g. 1,4,7")
    ap.add_argument("--name", default="fix",
                    help='what to call the destination track (default "fix"; '
                         'pass an empty string to leave its name alone)')
    a = ap.parse_args()

    def log(*s):
        print(*s); sys.stdout.flush()

    resolve = connect()
    pm = resolve.GetProjectManager()
    proj = pm.GetCurrentProject()
    if proj is None:
        sys.exit("No project is open.")

    tl = None
    if a.timeline:
        for i in range(1, proj.GetTimelineCount() + 1):
            t = proj.GetTimelineByIndex(i)
            if t.GetName() == a.timeline:
                tl = t
        if tl is None:
            sys.exit(f"No timeline called {a.timeline!r} in this project.")
        proj.SetCurrentTimeline(tl)
    else:
        tl = proj.GetCurrentTimeline()
    if tl is None:
        sys.exit("No timeline is open.")

    src_track, dst_track = a.track, a.track + 1
    log()
    log("=" * 70)
    log(f"  {proj.GetName()}  /  {tl.GetName()}")
    log(f"  reading V{src_track}, repairs go to V{dst_track}")
    log("=" * 70)

    clips = read_track(tl, src_track)
    if not clips:
        sys.exit(f"There are no clips on V{src_track}.")

    want = None
    if a.only:
        want = {int(x) for x in a.only.replace(" ", "").split(",") if x}

    # ---- 1. look at every clip first, and say so, before touching anything --
    import cadence_core as core
    todo, skipped = [], []
    log()
    for n, c in enumerate(clips, 1):
        if want and n not in want:
            continue
        why = placeable(c)
        if why:
            skipped.append((n, c["name"], why)); log(f"  {n:>3}. {c['name'][:44]:<44} skipped - {why}")
            continue
        try:
            v = core.diagnose(c["path"])
        except Exception as e:
            skipped.append((n, c["name"], f"unreadable: {e}"))
            log(f"  {n:>3}. {c['name'][:44]:<44} skipped - unreadable")
            continue
        if not v.ok:
            skipped.append((n, c["name"], f"refused: {v.refuse_kind}"))
            log(f"  {n:>3}. {c['name'][:44]:<44} skipped - refused ({v.refuse_kind})")
            continue
        if v.klass in ("CLEAN", "STATIC", "IRREGULAR"):
            skipped.append((n, c["name"], f"{v.klass} - nothing to repair"))
            log(f"  {n:>3}. {c['name'][:44]:<44} {v.klass} - leaving it alone")
            continue
        if v.klass == "AMBIGUOUS":
            skipped.append((n, c["name"], "AMBIGUOUS - the tool will not guess"))
            log(f"  {n:>3}. {c['name'][:44]:<44} AMBIGUOUS - not guessing")
            continue
        c["n"], c["klass"] = n, v.klass
        todo.append(c)
        log(f"  {n:>3}. {c['name'][:44]:<44} {v.klass}  -> will repair")

    log()
    log(f"  {len(todo)} to repair, {len(skipped)} left alone")
    if not todo:
        log("  Nothing to do."); return
    if a.dry_run:
        log("  --dry-run: stopping here, nothing was changed."); return

    # ---- 2. is there room above? ------------------------------------------
    while tl.GetTrackCount("video") < dst_track:
        tl.AddTrack("video")
    spans = [(c["rec"], c["rec"] + c["dur"]) for c in todo]
    busy = track_is_clear(tl, dst_track, spans)
    if busy:
        sys.exit(f"\n  V{dst_track} already has clips where the repairs need to go "
                 f"({', '.join(busy[:4])}...).\n"
                 f"  Clear that track, or point --track at a different one. Nothing "
                 f"has been changed.")
    if tl.GetIsTrackLocked("video", dst_track):
        sys.exit(f"\n  V{dst_track} is locked. Unlock it and run this again.")

    # Name it, so that six months from now the track says what is on it rather
    # than "Video 2". Only ever the destination track, and only when there is
    # something to put on it.
    if a.name:
        was = tl.GetTrackName("video", dst_track)
        if tl.SetTrackName("video", dst_track, a.name):
            log(f"  V{dst_track} renamed {was!r} -> {a.name!r}")
        else:
            log(f"  (could not rename V{dst_track}; carrying on)")

    # ---- 3. repair, once per FILE however many times it is cut in ----------
    done, failed = {}, []
    files = []
    for c in todo:
        if c["path"] not in files:
            files.append(c["path"])
    log()
    for k, src in enumerate(files, 1):
        log(f"  [{k}/{len(files)}] {os.path.basename(src)}")
        t0 = time.time()
        out, note = repair(src, a.crf, log)
        if out is None:
            log(f"      {note}"); failed.append((src, note)); continue
        bad = same_shape(src, out)
        if bad:
            log(f"      NOT placing it: {bad}")
            failed.append((src, bad)); continue
        done[src] = out
        log(f"      {note}  ({time.time() - t0:.0f}s)")

    if not done:
        log("\n  Nothing came back usable. The timeline is untouched.")
        return

    # ---- 4. import and place ----------------------------------------------
    mp = proj.GetMediaPool()
    folder = bin_named(mp, "Even frames")
    mp.SetCurrentFolder(folder)
    imported = mp.ImportMedia(sorted(set(done.values()))) or []
    by_path = {}
    for it in imported:
        by_path[it.GetClipProperty("File Path")] = it
    if not by_path:
        sys.exit("  Resolve would not import the repaired files. Timeline untouched.")

    infos, placed = [], []
    for c in todo:
        out = done.get(c["path"])
        item = by_path.get(out) if out else None
        if item is None:
            continue
        # endFrame is EXCLUSIVE here. Resolve gives you (endFrame - startFrame)
        # frames, not one more than that, so the obvious "+ dur - 1" quietly
        # drops the last frame of every clip - which nothing complains about,
        # because a clip one frame short still cuts, still plays, and still
        # lines up at its head. It shows up as a one-frame slip at the tail.
        infos.append({"mediaPoolItem": item,
                      "startFrame": c["src_in"],
                      "endFrame": c["src_in"] + c["dur"],
                      "trackIndex": dst_track,
                      "recordFrame": c["rec"],
                      "mediaType": 1})          # video only - the original keeps the sound
        placed.append(c)

    got = mp.AppendToTimeline(infos) if infos else []
    log()
    log("=" * 70)
    log(f"  placed {len(got or [])} of {len(infos)} repairs on V{dst_track}")
    for c in placed:
        log(f"      {c['n']:>3}. {c['name'][:50]}")
    if failed:
        log()
        log(f"  {len(failed)} not placed:")
        for src, why in failed:
            log(f"      {os.path.basename(src)[:46]:<46} {why}")
    log("=" * 70)
    log("  The originals on V%d were not touched. Switch V%d on and off to compare."
        % (src_track, dst_track))
    log()


if __name__ == "__main__":
    main()
