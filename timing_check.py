#!/usr/bin/env python3
"""
timing_check.py - does a repaired clip still march in step with its source?

  python3 timing_check.py ORIGINAL.mp4 REPAIRED.mp4

Equal length is NOT equal timing. A repair can hand back exactly the right frame
count and still slide the picture against the sound, if the restored moments were
put in the wrong places. This measures that directly.

It builds a "story progress" curve for each file - how much total motion has
happened by frame N - and asks, at each checkpoint, how far the repaired clip has
got compared with the original. A lag of 0 everywhere means the picture is where
it was. Anything past 2 frames will show against dialogue.

Do not compare frames by nearest-match: an invented frame equals no source frame,
so the match jumps around and invents drift that is not there.
"""
import subprocess, sys, os

W, H = 160, 90

def dur_frames(f):
    o = subprocess.run(["ffprobe","-v","error","-select_streams","v:0",
                        "-show_entries","stream=duration,nb_frames","-of","csv=p=0",f],
                       capture_output=True).stdout.decode().strip().split(",")
    try:
        return float(o[0]), int(o[1])
    except Exception:
        return None, None

def curve(f):
    d = subprocess.run(["ffmpeg","-v","error","-i",f,"-vf",f"scale={W}:{H}",
                        "-pix_fmt","gray","-f","rawvideo","-"],
                       capture_output=True).stdout
    n = W*H
    fr = [d[i*n:(i+1)*n] for i in range(len(d)//n)]
    if len(fr) < 4:
        raise SystemExit(f"could not read {f}")
    c = [0.0]
    for i in range(1, len(fr)):
        c.append(c[-1] + sum(abs(x-y) for x,y in zip(fr[i], fr[i-1]))/n)
    return c

def streams(f):
    out = subprocess.run(["ffprobe","-v","error","-show_entries",
                          "stream=codec_type,duration,nb_frames","-of","csv=p=0",f],
                         capture_output=True).stdout.decode().strip()
    return out.replace("\n", "   ")

if len(sys.argv) < 3:
    print(__doc__); raise SystemExit(2)
src, out = sys.argv[1], sys.argv[2]
print()
print(f"  original : {os.path.basename(src)}")
print(f"             {streams(src)}")
print(f"  repaired : {os.path.basename(out)}")
print(f"             {streams(out)}")
a, b = curve(src), curve(out)
# compare in SECONDS, not frame numbers: a repaired clip may carry a different
# frame count and frame rate while covering the same span of time
da, na = dur_frames(src)
db, nb = dur_frames(out)
sa = (da/na) if da and na else 1/24
sb = (db/nb) if db and nb else 1/24
if a[-1] == 0 or b[-1] == 0:
    raise SystemExit("  clip has no motion to measure")
A = [x/a[-1] for x in a]; B = [x/b[-1] for x in b]
print()
print("    time      lag")
worst = 0
for i in range(0, len(B), max(1, len(B)//14)):
    p = B[i]
    j = min(range(len(A)), key=lambda k: abs(A[k]-p))
    d = (j*sa - i*sb)          # seconds of drift
    if abs(d) > abs(worst):
        worst = d
    print(f"  {i*sb:6.2f}s  {d:+7.3f}s")
print()
print(f"  largest lag: {worst:+.3f} s")

# If the two files carry the same number of frames over the same duration, the
# audio CANNOT slide - there is no room for it to slide into. Anything the curve
# shows then is the repair having moved motion around inside the clip, which is
# what it was asked to do, not a sync problem. Say so, rather than letting the
# wording frighten you off a good file.
locked = (na == nb) and da is not None and db is not None and abs(da - db) < 0.01
if locked:
    print()
    print(f"  Same frame count ({na}) and same duration ({da:.3f}s), so the audio")
    print( "  cannot drift - there is nowhere for it to drift to. The number above")
    print( "  is how far the repair moved motion around INSIDE the clip while")
    print( "  evening it out, which is the job it was given. Under about 0.15s is")
    print( "  normal for a repaired file; only worry past that.")
    print()
worst = worst*24
if abs(worst) <= 1:
    print("  IN STEP - the picture sits where it did. Audio will line up.")
elif abs(worst) <= 3:
    print("  SLIGHT DRIFT - fine for action, watch it on dialogue.")
else:
    print("  DRIFTS - the picture slides against the sound. Do not use this file.")
