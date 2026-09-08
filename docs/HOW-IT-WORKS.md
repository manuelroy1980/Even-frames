# How Even Frames works

Rebuilt 2026-09-04. The long build log that got us here is in
`BUILD-HISTORY.md`; everything it concluded is still true, and the
places where it was wrong are called out below.

---

## The two buttons

    0-SETUP-CHECK.bat      is Python and ffmpeg installed
    1-FIX-VIDEO.bat        diagnose, then apply the one repair that fits
    2-DIAGNOSE-ONLY.bat    same diagnosis, writes nothing
    4-CHECK-TIMING.bat     does the repaired clip still fit the audio
    5-SELF-TEST.bat        prove the repairs still work, on known damage

Drag raw downloads onto **1-FIX-VIDEO**. It works out what is wrong and does the
matching repair. There is nothing to choose.

Output is `<name>_even.mp4` beside the original, with a `_even_REPORT.txt` saying
what was done.

---

## The rule the whole folder is built on

> **The output is always 24 fps, always the same number of frames, and always
> the same duration as the input.**

Never a 25 or 30 fps file to conform later. Never a few percent longer with a
retime note. Never a silent drift against the sound. A file that comes out of
here drops straight onto a 24 fps timeline beside its audio.

If a clip cannot be repaired under that rule, the tool says so and leaves it
alone. That is not a failure — it is the tool refusing to hand you a file that
looks fixed and is not.

---

## What was actually wrong before

The complaint that started this rebuild was *"it keeps applying solutions to the
wrong problems."* That was accurate, and there were five separate reasons.

**1. The checker and the repairer were two different programs that disagreed.**
`cadence_check.py` decided whether a clip was broken with a *spacing* test —
"are the gaps between the big steps even?" `cadence_fix.py` used a *periodicity*
test — "does the pattern repeat?" The history file records that the spacing test
was replaced because it was wrong; only the repairer was ever updated. So the
checker would tell you `mountain 2.mp4` was "bursts of fast motion, nothing to
repair" and the repairer would then rebuild the same file at 30 fps. Whichever
you believed, the other was doing something else.

**2. The recommendation engine in the checker was dead code.** `analyse()`
returned `phase = None`, `smear_trial()` compared `i % period == None`, which is
false for every frame, so the trial never ran. Every clip printed *"could not run
the trial"*, `risky` was therefore always false, and the answer was always
"use icon 2" — including on the clips that needed Apollo instead.

**3. Nothing was ever cut into pieces before the arithmetic.** Head-and-tail
freezes, held poses and hard cuts were all fed into the same frame-rate sum as
the real motion. `red hair man.mp4` opens on a held frame; those static frames
were dragged into the rate calculation, and the answer came out of the wrong end.

**4. Defects were treated as if they covered the whole clip.** `mountain 2.mp4`
is a 30-into-24 conversion, and the old repair re-rendered all 217 frames at
30 fps. It should only ever touch the frames that are actually damaged.

**5. The threshold was calibrated to a number that does not occur.**
Every version of these tools waited for a step about **2.0×** its neighbours,
on the reasoning that a missing frame means two frames of motion in one step.
That reasoning is wrong, because the measurement saturates: when a subject moves
twice as far, the pixels it uncovers do not double, because the two positions
stop overlapping. Measured against ground truth — a clean clip with individual
frames deliberately removed — **a genuinely missing frame reads 1.48 to 1.65,
and nothing in the untouched clip exceeds 1.30.** Waiting for 2.0 is why real
defects were being reported as "nothing to repair".

---

## Correction, 2026-09-05 — `walking forest.mp4`

An untouched download with an obvious repeat every fourth frame was refused as
*"already repaired"*. Four things had to be right for that to happen, and all
four were wrong.

**The packet vote was dead, so "two votes out of three" was really two out of
two.** A repeat was called when two of three tests agreed, and one of them
compared each frame's encoded size against a tenth of the clip's median. That
never fires on a re-encoded H.264 file with B-frames: a repeated frame there
still costs 20–50 kB, because it is coded against a future reference as well as
a past one, and a P-frame costs three times what a B-frame costs whatever is in
it. The vote now compares each frame only against others of **its own picture
type**, at a realistic threshold.

**The decisive measurement was being treated as one vote among equals.** On this
clip the repeats measure a `peak` of 0.55–0.91 against 3.8–11.2 for the moving
frames — a separation of five to twenty times, far cleaner than anything else
available. The frame average is much weaker: the same repeats read 0.09–0.35 of
their neighbours, and the flat cutoff at 0.22 caught six of the ten. `peak` is
now the primary evidence, in relative *and* absolute form, with the others as
corroboration.

**The beat detector shattered on the ones that were missed.** It required four
consecutive repeats spaced exactly `P` apart; with four of ten missing, no chain
reached four, so it concluded there was no padding at all. A chain may now step
over one missed repeat, provided solid links still outnumber bridges two to one.

**And the guard that refused the file was reasoning from the wrong premise.**
"No repeats, but a doubled step every four frames" was treated as the
fingerprint of a pre-repaired clip. It is the opposite: scattered doubles mean a
bad repair, but doubles on an exact four-frame beat are the *other half of
padding*, and mean the repeats are there and were missed. A tight beat in the
doubles now cancels the accusation outright.

Two more faults surfaced behind those:

- **One unpaired repeat re-rendered the whole clip.** If a repeat could not be
  matched to a doubled step, the span no longer landed on whole frames, every
  position in it became fractional, and a job needing 41 new frames repainted
  166 of 193. Unmatched repeats are now demoted to ordinary frames so the
  arithmetic closes, and a padded span is snapped to the 24 fps grid regardless.
- **Assembling the output doubled the disk needed.** Frames were copied into an
  output folder after extraction, so a job that checked its space, was told it
  had enough, and extracted all 97 frames then died with no room left. The
  output sequence is now built with hard links, which cost nothing.

Result on the file itself — the steps between frames, before and after:

    before   0.78  0.96  0.32  1.31  0.91  0.82  0.27  1.23  0.88  0.84  0.20  1.23
                         ^^^^  ^^^^              ^^^^  ^^^^              ^^^^  ^^^^
                         repeat, then the doubled step that paid for it

    after    0.80  0.96  0.77  0.75  0.91  0.81  0.70  0.75  0.89  0.82  0.67  0.70

Unevenness 0.87 → 0.23, worst hitch 1.98× → 1.35×, 21 doubled steps → 2, and
24 of 97 frames repainted with 73 copied through untouched.

---

## The self-test, and what it found on its first run

These tools do not learn. They are a Python program: no memory between runs, no
adaptation. Every improvement in this file is a change I made to the code after
measuring something. What follows is the machinery that makes the *next* change
safe, which is a different and more achievable thing.

`5-SELF-TEST.bat` takes a clip with nothing wrong with it, makes a small copy,
breaks that copy in four ways it already knows the answer to, and checks the
tools get each one right and put it back. It does not find new kinds of damage —
only a real clip can do that. It finds the tools being broken by a change meant
to improve them, which is a real risk and had already happened once: fixing the
repeat detector so it would see `walking forest.mp4` made the tools start
diagnosing their own repaired output as freshly damaged.

Its first run failed two of four, and all three causes were genuine.

**The baseline was measured over too wide a window.** A step was judged against
the median of the twenty-one around it. That fails on a shot that accelerates —
the test clip climbs from 2.0 to over 12 across four seconds, so the window
mixes early steps with late ones. And it fails again on a padded clip, where a
third of the surviving steps are the doubles themselves and drag the median up
towards their own size. Both at once was fatal: a doubled step of 4.46 against
neighbours of 2.7 and 3.0 — plainly twice their size — scored 1.05. The baseline
is now the nearest two real steps either side, close enough that acceleration
cancels out of it.

**The measurement saturates on fast motion, so it is now taken at two scales.**
A mean absolute difference stops being proportional once a moving subject's two
positions no longer overlap — past that point, moving twice as far adds almost
nothing. The same five deliberately-removed frames measured:

    at 192x108   1.64  1.32  1.51  1.46  1.45      one of them invisible
    at  96x54    1.88  1.46  1.58  1.54  1.51      all five detectable

Halving the resolution halves the displacement in pixels and puts the number
back in its proportional range. So repeats are now judged on the fine detail,
where small movement shows, and missing frames on the coarse, where large
movement still counts. The coarse signal is derived from the fine one by
averaging 2x2 blocks, so it costs no extra decoding.

**The flash test was eating real defects.** It flagged any step where the mean
brightness moved more than six times the clip's usual wobble — and a camera
pushing towards a light source does that constantly. On the test clip it flagged
three steps, two of which were frames I had deliberately removed, so the tool
skipped over its own damage and called the clip fine. The fix is to test the
*ratio*: in a fade every pixel shifts the same way, so the brightness change is
most of the whole frame difference; in motion, pixels move both ways and cancel.
Across that clip the ratio runs 0.05 at the median and never exceeds 0.34, while
a fade sits near 1.0. The bar is now 0.5.

With those three fixed, all four cases pass, and two real clips got better as a
side effect: `red hair man.mp4` went from 6 detected hitches to 8, and the extra
two sit exactly on the ~19-frame spacing of the other six.

    control    read as CLEAN, left alone
    pad        PAD period 4, 23 of 24 repeats     unevenness 1.35 -> 0.41
    seam       SEAM, named 4 of the 5 removed     unevenness 0.47 -> 0.34
    pulldown   PULLDOWN, 29 fps source, 93% fit   unevenness 0.88 -> 0.37

All four came back with the frame count, rate and duration untouched, and all
four re-read as CLEAN afterwards.

The one repeat the `pad` case misses sits directly beside a frame where the
scene genuinely stopped moving. Two identical frames in a row are two identical
frames in a row; nothing in the pixels says which one was padding. It is left
alone, which is the safe answer.

## The log

Every run of either button appends one line to `cadence-log.tsv` beside the
scripts: the verdict, the evidence scores, the beat, the implied source rate,
how many frames were repainted, and the before and after numbers.

This is not the tool learning either. It is the evidence trail, so that when a
clip does come out wrong the numbers that produced the wrong answer are already
on disk instead of having to be re-derived from a description of what went
wrong. Recalibrating against a hundred of your own clips is a different
proposition from recalibrating against the dozen I happened to be shown.

---

## How it is organised now

One engine, `cadence_core.py`. Both buttons import it and read the same verdict,
so they cannot tell you two different stories about the same clip.

    1  READ THE CONTAINER   frame rate, duration, audio, encoder tags, and
                            whether the frame timestamps are even. A clip that
                            is not 24 fps, or that is variable frame rate, has a
                            container problem, and no interpolator fixes that.
                            Caught first, before a pixel is looked at.

    2  MEASURE THE PIXELS   one decode pass, three numbers per step, not one:
                              step  how much the frame changed overall
                              peak  how much the busiest 12x12 block changed
                              lum   how much the brightness changed
                            One number cannot separate a repeat from a slow pan,
                            or a cut from a missing frame. Three can. A fourth
                            check reads the encoded size of each frame straight
                            out of the file - a frame that is a copy costs the
                            encoder almost nothing, which is evidence that does
                            not come from the pixel measurement at all.

    3  CUT THE CLIP UP      hard cuts, a held opening, a held ending and any
                            freeze in the middle are found and set aside BEFORE
                            any arithmetic. Nothing is ever interpolated across
                            a cut.

    4  CLASSIFY EACH REGION separately, against evidence, with a margin. Every
                            hypothesis is scored; the winner must beat the
                            runner-up. When nothing wins clearly the answer is
                            AMBIGUOUS and the repair stops rather than guessing.

    5  PLAN THE REPAIR      as target positions on a motion timeline. Every
                            class produces the same shape of answer, so there is
                            one renderer and one place to get it wrong.

    6  CHECK ITS OWN WORK   the repaired file is measured and re-diagnosed. If
                            the engine still recognises the defect, or the clip
                            is no more even than it was, it says so in capitals.

### The classes, and the repair each one earns

| class | what it is | repair | frames repainted |
|---|---|---|---|
| `CLEAN` | motion is already even | none | 0 |
| `STATIC` | a held pose; the scene really stops | none | 0 |
| `PAD` | every Nth frame is a copy — the real one was lost | repaint the wasted slot in place | ~1 in N |
| `SEAM` | a handful of isolated missing frames | even out 8 frames either side of each | ~16 per hitch |
| `PULLDOWN` | frames thrown away throughout, on a confirmed repeating cycle | rebuild the damaged stretch at the source rate | most of that stretch |
| `IRREGULAR` | big steps, but they ramp with their neighbours — fast motion | none | 0 |
| `AMBIGUOUS` | two explanations fit equally | none, and it says why | 0 |

The width of the correction is chosen to match how far the defect reaches. That
is the difference between fixing a clip and re-rendering it.

### PAD and SEAM and PULLDOWN are one operation

The core hands over two numbers per frame: where the frame **is** on the motion
timeline, and where it **should** be. Making that true is a single loop —
if a real frame already sits at the wanted position, copy it untouched;
otherwise paint it from the two real frames either side. Because every
correction is written as a span whose two ends are pinned to real frames, the
frame count, the frame rate and the duration cannot change.

### Why PULLDOWN needs a confirmed cycle

The two repairs behave very differently when the detector misses something.

With a confirmed repeating cycle, drops we did not see are almost certainly
there too, so the whole stretch is rebuilt from the source rate and a missed
drop costs a fraction of a percent of speed, spread smoothly — invisible.

Without a cycle, the drops we found are all there is to go on, and rebuilding the
whole stretch from a guessed rate moves every frame slightly for no reason. That
was measured: forcing a five-hitch clip down the PULLDOWN path made its worst
hitch **worse**, 1.91× → 2.55×. Sent down the SEAM path instead, the same clip
went 1.91× → 1.48×. So `PULLDOWN` now requires a cycle that fits at 70% or
better; everything else is handled one window at a time.

---

## Proof that it works

A clean 97-frame clip was damaged in three known ways and repaired. The tool was
never told what had been done to it.

| test | what was done | diagnosed as | unevenness | worst hitch |
|---|---|---|---|---|
| control | nothing | `CLEAN`, left alone | 0.051 | 1.06× |
| pad | every 4th frame replaced by a copy | `PAD`, period 4, 24 repeats, beat 100% solid | 0.984 | 2.01× |
| pad, repaired | | re-diagnosed `CLEAN` | **0.073** | **1.09×** |
| drop | 3 frames removed | `SEAM` ×3 | 0.068 | 1.76× |
| drop, repaired | | re-diagnosed `CLEAN` | | **1.29×** |
| seam | 5 frames removed at 17, 38, 59, 74, 88 | `SEAM` ×5, named as **17, 37, 57, 71, 84** | 0.136 | 1.51× |
| seam, repaired | | re-diagnosed `CLEAN` | 0.130 | **1.24×** |

Those five frame numbers are exactly the five that were removed, once you account
for the earlier removals shifting the later ones. The detector found precisely
the damage, and nothing else.

The repaired pad clip reads 0.073 and 1.09× against a control that reads 0.051
and 1.06×. It is statistically indistinguishable from a clip that was never
damaged — which is the bar, and the old two-pass chain did not come close to it.

### The two numbers in the CHECK block

    unevenness   the 90th-percentile disagreement between a step and the steps
                 either side of it. Catches a fault in every frame - padding,
                 a rate conversion.
    worst hitch  the third-largest disagreement. Catches a fault in three frames
                 out of ninety, which barely moves a percentile but is the thing
                 you actually see. Third-largest and not largest, because a clip
                 that opens on a held pose has one honest jump at the moment it
                 starts moving, and that jump should not stand for the clip.

A clip with nothing wrong reads about **0.05** and **1.1×**. Both are judged
against the immediate neighbours, not a wide average, so a shot that accelerates
or slows down does not score as uneven.

---

## Reading the real clips

    mountain 2.mp4      PULLDOWN   50 drops, cycle of 25 frames, 94% fit
                                   -> a 29.58 fps source, rebuilt as 29.97
    red hair man.mp4    SEAM       6 isolated hitches before frames 19, 56, 76,
                                   91, 110, 144; 86 of 145 frames repainted,
                                   the rest copied
    hf_...1f8ede56      PAD        every 4th frame repeated, 33 of them, beat
                                   89% solid, plus a hard cut at frame 167 -
                                   the 25 frames after the cut are handled
                                   separately and left alone
    ramos top pyramid   CLEAN      nothing to do

---

## Things it now refuses to do, and why

- **Already processed.** `_rebuilt`, `_seamfix`, `_cadence`, `_even`, `_apo8`,
  `_ganim` and friends in the filename. The repair reads the original defect to
  work out what happened; an earlier pass has erased it. `--force` overrides.
- **Not 24 fps.** Every measurement here assumes 24. On a 25 or 30 fps file they
  would be timed wrong. Convert first, or pass `--fps`.
- **Variable frame rate.** The stutter is in the timestamps, not the pictures.
  Re-mux to constant frame rate; no interpolator can help.
- **Looks pre-repaired.** No padding anywhere, yet a doubled step every few
  frames. Real footage does not look like that; a clip repaired once and never
  retimed does.
- **Ambiguous.** Two explanations within 0.12 of each other. It prints both
  scores and stops. `--force-class PAD|SEAM|PULLDOWN` if you know better.

---

## When ffmpeg is not good enough

Every repair measures both ways of painting an invented frame — optical flow and
a cross-fade — on *this clip*, and keeps whichever damages the worst areas less.
The number is printed.

If neither keeps more than 55% of the detail in its worst areas, the motion
between frames is past what block matching can follow, and the output says so.
That is the only case where **Topaz Apollo (apo-8, "replace duplicate frames")
on the raw download** is worth the trip. The ffmpeg result is still written, as
the fallback.

---

## Files

    cadence_core.py     the engine: probe, measure, segment, classify, plan
    cadence_fix.py      diagnose -> repair -> verify        (1-FIX-VIDEO)
    cadence_check.py    diagnose only                       (2-DIAGNOSE-ONLY)
    run_tool.py         drag-and-drop launcher for the .bat icons
    timing_check.py     drift check against the source      (4-CHECK-TIMING)
    cadence_selftest.py known damage, known answers         (5-SELF-TEST)
    cadence-log.tsv     one line per clip the tools have seen
    _retired/           the four old single-purpose tools, kept for reference

Set `CADENCE_TMP` to choose the scratch folder; frame extraction needs roughly
3 MB per 1080p frame and 12 MB per 4K frame. Set `CADENCE_DEBUG=1` to see the
ffmpeg errors behind any frame that fails to paint.

Needs python3 and ffmpeg, nothing else. numpy is used if it happens to be
installed and is not required.
