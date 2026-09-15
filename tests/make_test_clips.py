#!/usr/bin/env python3
"""Build synthetic clips with known faults.

  python3 make_test_clips.py [output folder] [only these cases]

Needs Pillow (pip install pillow) as well as ffmpeg. It is a development tool -
the engine itself needs neither.


Every clip is the same even 24 fps source - a smooth, non-repeating texture
panning at a constant speed - with one fault put into it deliberately. Because
the motion is constant by construction, anything the engine measures that is not
constant is the fault, and nothing else.

Two details matter or the test lies:

  the texture   must be smooth and must not repeat. A generated pattern
                (testsrc2 and friends) does both: it has flat passages, so
                panning across one produces frames that genuinely ARE near
                copies, and the engine then correctly reports repeats the test
                never asked for. Blurred random noise has no period.

  the pan       must be SUB-PIXEL and must be slow. Rounding each frame's offset
                to a whole pixel quantises a 14% wobble in a 3-pixel step down to
                nothing, and panning fast enough to avoid that puts consecutive
                frames past the point where they have anything in common - where
                the difference between two frames stops growing with the distance
                between them and every step measures the same. Both failures look
                exactly like a clean clip.
"""
import os, sys, math, random, subprocess, shutil
from PIL import Image

OUT = sys.argv[1] if len(sys.argv) > 1 else "clips"
W, H, N = 480, 270, 168          # 7 seconds at 24 fps
PX = 3.0                         # pixels of pan per frame of motion
os.makedirs(OUT, exist_ok=True)
FR = os.path.join(OUT, "_frames")


def run(cmd):
    r = subprocess.run(cmd, capture_output=True)
    if r.returncode:
        print(" ".join(cmd[:14]), "...")
        print(r.stderr.decode()[-1200:])
        raise SystemExit(1)


def plate():
    wide = int(W + PX * (N + 40) + 80)
    random.seed(7)
    sw, sh = wide // 5 + 2, H // 5 + 2
    small = Image.frombytes("RGB", (sw, sh),
                            bytes(random.getrandbits(8) for _ in range(sw * sh * 3)))
    return small.resize((wide, H), Image.BICUBIC)


PLATE = plate()


def build_frames(positions):
    shutil.rmtree(FR, ignore_errors=True)
    os.makedirs(FR)
    # every clip starts at the same place in the pan, so a repaired clip can be
    # compared frame for frame against the clean one it should have been
    base = positions[0]
    positions = [p - base for p in positions]
    for i, p in enumerate(positions):
        PLATE.transform((W, H), Image.AFFINE, (1, 0, p * PX, 0, 1, 0),
                        resample=Image.BILINEAR).save(os.path.join(FR, f"{i:05d}.png"))


def encode(name):
    out = os.path.join(OUT, name)
    run(["ffmpeg", "-v", "error", "-y", "-framerate", "24",
         "-i", os.path.join(FR, "%05d.png"),
         "-c:v", "libx264", "-crf", "12", "-preset", "veryfast",
         "-pix_fmt", "yuv420p", "-g", "24", out])
    print("  wrote", name)
    return out


# --------------------------------------------------------------- the faults

def clean():
    build_frames([float(i) for i in range(N)])
    return encode("clean.mp4")


def padded(period=4):
    """every 4th frame is a copy of the one before it - an 18 fps render
    stretched onto the 24 fps grid."""
    pos, t = [], 0.0
    for i in range(N):
        if i and i % period == 0:
            pos.append(pos[-1])
        else:
            t += 1.0
            pos.append(t)
    build_frames(pos)
    return encode("padded.mp4")


def held(where=(40, 95, 130)):
    """a frozen frame in an otherwise even clip, with a real frame lost to pay
    for it, so the frame count never changes."""
    pos, t = [], 0.0
    for i in range(N):
        if i in where:
            pos.append(pos[-1])
            t += 1.0
        else:
            t += 1.0
            pos.append(t)
    build_frames(pos)
    return encode("held.mp4")


def grid(amp=0.14, name="grid.mp4"):
    """no frame repeated anywhere: the steps simply alternate long/short."""
    pos, t = [], 0.0
    for i in range(N):
        pos.append(t)
        t += (1 + amp) if i % 2 == 0 else (1 - amp)
    build_frames(pos)
    return encode(name)


def grid_small():
    """below the floor: real, but not worth repainting a frame over."""
    return grid(0.035, "grid_small.mp4")


def pulldown():
    """30 fps of motion forced into 24 - every 5th frame thrown away."""
    pos, k = [], 0
    while len(pos) < N:
        if k % 5 != 4:
            pos.append(float(k))
        k += 1
    build_frames(pos)
    return encode("pulldown.mp4")


def mixed():
    """padded for the first 50 frames, a genuine freeze, then clean to the end.

    The clean stretch is more than twice the padded one on purpose: this is the
    clip that asks whether one bad region gets outvoted by good ones.
    """
    pos, t = [], 0.0
    for i in range(N):
        if i < 50 and i and i % 4 == 0:
            pos.append(pos[-1])           # padded
        elif 50 <= i < 58:
            pos.append(pos[-1])           # the scene genuinely stops
        else:
            t += 1.0
            pos.append(t)
    build_frames(pos)
    return encode("mixed.mp4")


def fast():
    """real fast motion in bursts: big steps, nothing wrong."""
    pos, t = [], 0.0
    for i in range(N):
        pos.append(t)
        t += 1.0 + 2.2 * math.exp(-((i % 40 - 20) / 3.5) ** 2)
    build_frames(pos)
    return encode("fast.mp4")


ALL = (clean, padded, held, grid, grid_small, pulldown, mixed, fast)

if __name__ == "__main__":
    only = sys.argv[2:] if len(sys.argv) > 2 else None
    for fn in ALL:
        if only and fn.__name__ not in only:
            continue
        print(fn.__name__)
        fn()
    shutil.rmtree(FR, ignore_errors=True)
