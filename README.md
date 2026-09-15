# Even Frames

**Repairs the stutter in AI-generated video.**
Free, open source, Windows and macOS. You do not need to know how to code.

Clips out of Seedance, Kling, Veo, Runway, Hailuo and the rest very often do not
run at an even 24 frames a second, even though the file says they do. Something
in the generation or the export threw a frame away, or repeated one, or squeezed
a 30 fps render onto a 24 fps grid. You see it as a hitch about once a second. On
a timeline next to clean footage it is the first thing anyone notices.

This fixes it. Drag the file on, wait, get a repaired file back.

```
    <your clip>.mp4  ->  <your clip>_even.mp4
```

**The result is always 24 fps, always the same number of frames, and always the
same duration as the original.** It drops straight back onto your timeline and
the audio still fits. If a clip cannot be repaired under that rule, the tool
says so and leaves your file alone.

---

## Install

You need two free programs: **Python** and **ffmpeg**. Neither has a window; they
run behind the scenes.

**Windows**

```
winget install Gyan.FFmpeg
winget install Python.Python.3.12
```

Then close that window, open a new one, and double-click `0-SETUP-CHECK.bat`.

**macOS**

```
brew install ffmpeg python
```

Then double-click `0-SETUP-CHECK.command`. If macOS refuses to open it, see
`READ-ME-FIRST-MAC.txt` — one right-click gets past it.

`READ-ME-FIRST-WINDOWS.txt` and `READ-ME-FIRST-MAC.txt` walk through all of this
slowly, with nothing assumed.

---

## Use

| | Windows | macOS |
|---|---|---|
| Is everything installed? | `0-SETUP-CHECK.bat` | `0-SETUP-CHECK.command` |
| **Fix a clip** | `1-FIX-VIDEO.bat` | `1-FIX-VIDEO.command` |
| Just tell me what is wrong | `2-DIAGNOSE-ONLY.bat` | `2-DIAGNOSE-ONLY.command` |
| Does the repair still fit the audio? | `4-CHECK-TIMING.bat` | `4-CHECK-TIMING.command` |
| Do the tools themselves still work? | `5-SELF-TEST.bat` | `5-SELF-TEST.command` |

Double-click the fix icon, drag one or more video files into the window that
opens, press Enter. Or from a terminal:

```
python cadence_fix.py "clip.mp4"          # diagnose and repair
python cadence_check.py "clip.mp4"        # diagnose only, writes nothing
```

**Use the raw download.** Not a file you have already run through an upscaler,
and not one this tool has already repaired. The repair works by reading the
original damage; an earlier pass has erased the evidence it needs. The tool
checks for this and refuses rather than guessing.

---

## What it looks for

Five things can be wrong with a clip's cadence, they look identical to the eye,
and they need different repairs. Doing the wrong one makes the clip worse — which
is the whole reason this is one tool with one button rather than a folder of
tools you choose between.

| what it finds | what that is | what it does |
|---|---|---|
| `PAD` | every Nth frame is a copy of the one before it; the real frame was thrown away | repaints the wasted slot in place, ~1 frame in N |
| `SEAM` | a held frame, or a handful of frames simply missing | repaints the frozen slot — one frame each |
| `PULLDOWN` | a 25 or 30 fps render squeezed into 24 by dropping frames all the way through | rebuilds the damaged stretch at the real source rate |
| `GRID` | nothing repeated and nothing missing, but the steps alternate long-short on a strict beat | moves every frame back onto an even grid |
| `CLEAN` `STATIC` `IRREGULAR` | nothing wrong: even motion, a deliberate freeze, or genuine fast motion | nothing at all |

It tells you which, and what the evidence was, before it touches anything —
and `2-DIAGNOSE-ONLY` shows you the same reasoning without writing a file.

---

## What it will not do

This is the part most tools leave out, and it is the part that makes the rest
trustworthy.

- **It will not change the length of your clip.** Not by one frame. If the only
  possible repair would make the clip longer, it refuses and tells you why.
- **It will not overwrite anything.** A second repair of the same clip becomes
  `_even_2`, never a replacement, so you can always compare.
- **It will not guess between two explanations.** When nothing in the clip
  favours one reading over another, it says so and stops.
- **It marks its own failures.** Every repair is measured and re-diagnosed
  afterwards. If the invented frames came out as double images, or the clip is
  no more even than it was, the file is written as `_even_REJECTED` and the tool
  exits with an error. Keep your original.
- **It will not work on a file that has already been processed.** See above.

---

## A whole timeline at once (DaVinci Resolve Studio)

`resolve/` holds a companion script that reads the clips on a video track of the
timeline you have open, repairs the ones that need it, and stacks each repair on
the track **directly above** its original — so you toggle the top track on and
off to compare. Nothing on the source track is touched.

    Windows   resolve\EVEN-FRAMES-IN-RESOLVE.bat
    macOS     resolve/EVEN-FRAMES-IN-RESOLVE.command

Requires Resolve **Studio** — the free version has no scripting API — and
*Preferences → System → General → External scripting using: Local*. It is slow,
minutes per clip, and it runs outside Resolve on purpose so you get a progress
report instead of a frozen application. `resolve/README.md` has the details.

---

## Where the repaired frames come from

Where a frame has to be invented, it is painted from the two real frames either
side — by optical flow or by averaging, whichever measures *less damaging on
that particular clip*. Both are tried, on that clip, and the choice is printed
with the numbers behind it.

An invented frame is then checked three ways: for double images (two pictures
superimposed — the characteristic failure of averaging over fast motion), for
fine detail against the frames beside it, and for whether the motion actually
came out even. If your clip moves faster than optical flow can follow, it says
so rather than handing you mush with an encouraging word on it.

---

## Files

```
cadence_core.py        the engine: measures, classifies, plans. All the thinking.
cadence_fix.py         diagnose, then apply the one repair the clip earned
cadence_check.py       diagnose only
cadence_selftest.py    break a known-clean clip six ways, check the repairs
run_tool.py            the launcher the icons call
timing_check.py        does a repaired clip still line up with its original
resolve/               repair a whole timeline track (Resolve Studio only)
cadence-log.tsv        written as you go: one row per clip per run
docs/                  how it works, and the history of how it got here
```

`cadence-log.tsv` is worth knowing about. Every run appends a row with the
verdict, the scores behind it, the frame counts, and the before/after
measurements. When a clip comes out wrong, the numbers that produced the wrong
answer are already on disk.

Needs only Python 3 and ffmpeg. No numpy, no pip install, nothing to build.

---

## Licence

MIT. Do what you like with it.

## Credits

Built for the *Moon Phase Chronicles* AI-film project, where the stutter was
costing more time than the shots were. Released free in the hope it saves
someone else the same afternoon.
