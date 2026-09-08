> **ARCHIVE.** This is the build log for these tools up to 2026-09-04, kept
> because the reasoning in it is still worth reading and several of the
> measurements are still the best evidence we have. It is not a description of
> how the tools work now — see `README.md` for that.
>
> Two things below turned out to be wrong and are corrected in `README.md`:
> a missing frame does **not** produce a step twice the size of its neighbours
> (it measures 1.48–1.65x, because the pixel difference saturates), and a
> dropped-frame clip must **not** be rebuilt at its source rate unless the drop
> pattern repeats on a confirmed cycle.

---

# Frame cadence diagnosis — Rush / Regeneration

Measured 2026-08-25 on the 10 exports in this folder (ffmpeg frame-by-frame diff,
downscaled to 160x90 grey, mean absolute pixel delta per frame pair).

All ten files are 24 fps CFR, constant frame duration, no timing weirdness in the
container. All ten carry a Topaz Video AI tag (`ganim-1`, plus `apo-8` on the two
skip-frame tests). So whatever you are seeing is baked into the pixels, not the
timecode — re-exporting or re-wrapping will not remove it.

There are **two different artifacts** in this folder, and they need different fixes.

---

## Artifact A — held frame every 4th frame (18 fps in a 24 fps wrapper)

Found in: `fight 1.mp4`, `woman take 2.mp4`

`woman take 2.mp4`, 241 frames: frames 3, 7, 11, 15 … 239 are near-identical
copies of the frame before them. Perfectly periodic, `n % 4 == 3`, 60 held frames
out of 241. That leaves 181 frames of real motion over 10.04 s = **18 fps of real
motion padded to 24 fps by holding every 4th frame.**

`fight 1.mp4` shows the same period-4 pattern (and at 8K it is very visible).

This is the artifact the community has documented for Seedance — see sources at
the bottom. Reported there as ~16 fps / every 3rd frame; what is in this folder is
18 fps / every 4th frame, same mechanism.

**Fix:** drop the held frames, then retime 18 → 24 with optical flow.

- Best quality: `fix_cadence.py <file> --mode clean` writes a true-18 fps file.
  Drop it on the 24 fps timeline in Resolve, set Retime Process = Optical Flow
  (Speed Warp for faces / fast action). Resolve synthesises the missing frames.
- No NLE: `fix_cadence.py <file>` (default `--mode interp`) does the same with
  ffmpeg `minterpolate`. Slow at 4K (~10 min for a 10 s clip) and it can smear on
  fast action, but it needs nothing else installed.
- Topaz's own Apollo (`apo-8`) with "replace duplicate frames" does this too —
  that is what the two skip-frame tests are.

---

## Artifact B — a double-step every ~20 frames

Found in: essentially every clip, **including the two Apollo passes**.

| file | double-steps at frame |
|---|---|
| procession walks | 19, 39, 58, 79, 99, 120 |
| skip frame test 1 | 58, 79, 99, 120 |
| skip frame test 2 | 58, 79, 99, 120 |
| mountain range | 20, 39, 59, 82, 101, 120 |
| crowd on bridge | 42, 62, 82, 103, 125 |
| precession 2 | 62, 81, 102 |

Spacing is 19–21 frames, i.e. **every ~0.83 s** — this is the "every second or so"
hitch. `procession walks.mp4` has no duplicate frames anywhere; its problem is
purely this.

Measured at the seam (frame 58 of `procession walks`): the frame-to-frame delta is
10.26 against a 5.4 baseline — almost exactly **two frames' worth of motion in one
step**. Mean luminance is smooth across the seam, so it is not a flicker or an
exposure pulse. One frame of motion is *missing*, not duplicated. On screen a
missing frame and a held frame feel much the same: a hitch.

Apollo does not touch it, because there is no duplicate there to replace.

**Likely causes, in order of how easy they are to test:**

1. **Topaz `ganim-1` chunk boundary.** Every file here has been through it, and
   the seam period (~20 frames) looks like a processing window. *Test: run
   `cadence_check.py` on the raw pre-Topaz download.* If the seams are absent
   there, stop using ganim-1 for these shots or switch enhancement model.
2. **Generator latent-chunk seam.** Video diffusion models generate in temporal
   segments; 121 frames ÷ 6 seams ≈ 20-frame segments is consistent with that.
   If it is this, it cannot be filtered out — it has to be masked or regenerated.

**Fix, if it survives step 1:** you cannot decimate a frame that is not there.
Either
- optical-flow reconstruct the whole clip (Topaz Chronos / Apollo at 2x to 48 fps,
  then conform back to 24), which resynthesises motion across the seam, or
- patch each seam individually in Resolve: a 2-frame optical-flow retime over the
  seam frame, ~6 patches per 5 s shot.

---

## Generation-side advice

- Pull the model's native master **before** any upscale, and check it with
  `cadence_check.py` first. Diagnose on the original, not on a 4K/8K derivative.
- Keep shots short (≤ 5–8 s). Both artifact types accumulate with length.
- Request 24 fps explicitly in the prompt if the aggregator honours it
  ("cinematic 24 fps cadence" is the commonly suggested wording), and prefer an
  aggregator that lets you download the raw model output.
- Fast lateral motion and pans expose both artifacts; slow moves hide them. The
  clips here with the worst readings are the fight/action ones.

---

## Tools in this folder

    python3 _tools/cadence_check.py *.mp4          # diagnose
    python3 _tools/fix_cadence.py FILE --mode clean   # dedupe -> true-fps file for Resolve
    python3 _tools/fix_cadence.py FILE                # dedupe + ffmpeg optical-flow retime to 24

`cadence_report.txt` is the run from 2026-08-25.

## Sources

- Dr. Dreams, X — "If you're using Seedance, your videos often have duplicate
  frames baked in — every 3rd frame is a held copy … Seedance runs at ~16 fps of
  real motion packed into a 24 fps container."
  https://x.com/DrDreams/status/2059770786853044359
- Ryan Lightbourn, X — sharing that fix for "the frame rate issue in Seedance 2.0".
  https://x.com/ryanlightbourn/status/2059776417940791730
- WaveSpeed, *How to Fix Flicker, Jitter, and Temporal Artifacts in Seedance 2.0* —
  covers flicker/jitter, not cadence; recommends naming "24 fps cadence" in prompts.
  https://wavespeed.ai/blog/posts/blog-fix-flicker-jitter-seedance-2-0/
- fal.ai Seedance 2.0 API reference — no output frame rate documented.
  https://github.com/fal-ai/seedance-2.0-api
- APIFrame Seedance 2.0 docs — durations 4–15 s, 480p–4K, no fps stated.
  https://apiframe.ai/docs/videos/seedance/seedance-2

---

# UPDATE 2026-08-25 — Artifact B solved

Tested the raw pre-Topaz download in `test reframing original size\`
(`magnific_handheld-camera-sfx-faint_p8m8sRKehw.mp4`, 1080p, 121 frames, 24 fps).

**Topaz is exonerated.** The raw file has the identical seams — steps at frames
19, 58, 79, 99, 120 measuring 1.31x / 1.77x / 1.96x / 1.96x / 1.97x the median.
~2.0x means exactly two frames of motion in one step. Same positions as the Topaz
4K version, so ganim-1 is faithfully passing through a defect that is already in
the Magnific/Seedance delivery.

**What it is: a 25 fps generation delivered in a 24 fps container.** Put the 5
missing frames back and you get 126 frames over 5.0417 s = **24.992 fps**. That is
not a coincidence — the clip is generated at 25 fps and 5 frames are dropped to
force it into 24 fps, one drop roughly every 20 frames. Naive 25 -> 24 conversion,
no optical flow.

There are also NO duplicate frames in this clip, so `fix_cadence.py` correctly
skips it. The 18 fps dedupe applies only to `fight 1` and `woman take 2`.

## seam_fix.py — the repair for this one

    python3 _tools/seam_fix.py FILE [--thresh 1.3] [--crf 14] [--dry-run]

Finds each doubled step, synthesises the missing in-between frame with optical
flow (ffmpeg minterpolate over a short window around the seam), and rebuilds the
clip at its true rate with the original duration.

Verified on the test file: 121 -> 125 frames, all interior seams gone. Only the
seam on the very last frame survives, because there is no frame after it to
interpolate towards — inaudible in practice.

Two outputs are in `test reframing original size\`:

- `..._seamfix.mp4` — 125 frames tagged 24.793 fps, same 5.042 s duration.
  Drop on a 24 fps timeline, Retime Process = Optical Flow. Correct speed.
- `..._seamfix_24p.mp4` — the same 125 frames played straight at 24 fps.
  5.208 s, motion runs 4% slower, zero resampling artifacts. For a 5-second
  cutaway this is usually the better-looking of the two. Compare them.

Run seam_fix.py on the RAW download, then send the repaired file to Topaz. Never
the other way round.

## UPDATE — 24p is now the default output of seam_fix.py

After A/B-ing the two versions on the test clip, straight 24p won.
`seam_fix.py` now writes `<name>_seamfix_24p.mp4` by default: the repaired
frame sequence played at exactly 24 fps. Duration grows ~4% (5.042s -> 5.208s)
and motion runs ~4% slower, but there is zero resampling and no NLE step.

`--true-rate` restores the old behaviour: `<name>_seamfix.mp4` tagged at the
clip's real rate (24.79 fps here), original duration, for Optical Flow retiming
in Resolve. Use it when a shot has to hit an exact duration or sync to audio.

## UPDATE — detector bug fixed (over-repair / local slow-motion)

Symptom: `procession bridge_seamfix_24p.mp4` played fine for three quarters then
crawled through the last quarter.

Cause: seams were judged against the clip's GLOBAL median step size. `procession
bridge` accelerates — the median step grows from ~3.0 early to ~5.2 late — so from
frame 110 on, ordinary fast motion cleared the threshold. 32 "seams" were detected
instead of 7, and 26 phantom frames were injected into the last quarter. In 24p
mode every inserted frame adds 1/24 s at that point, so that section stretched.
145 -> 176 frames, 6.04 s -> 7.33 s, +21% concentrated at the end.

Fix: seams are now judged against a rolling median of the 16 neighbouring steps.
A real dropped frame is ~2x its immediate neighbours wherever it sits; ordinary
fast motion is ~1.0x its neighbours by definition. On `procession bridge` the
local ratios are 1.47, 2.06, 1.98, 1.91, 1.89, 1.75, 1.72 with everything else at
~1.0 — 7 seams, spacing 21/20/20/21/22/19, real rate 25.16 fps. Textbook.

Also added: a safety cap of one repair per 12 frames. If detection exceeds it the
threshold is raised automatically and the adjustment is printed, so a clip can
never again be silently stretched. And the seam windows now render in parallel,
which roughly halved the runtime.

`cadence_check.py` uses the same local test now.

### Files made with the buggy detector — re-run these

    procession bridge   145 -> 176  (37 too many)   REDONE, now 151
    hf_20260712_0123..  145 -> 182  (30 too many)   re-run
    procession mid      121 -> 130  (3 too many)    re-run
    procession cliff    121 -> 126  (correct)       fine as is

## UPDATE — held frames are LOST frames. Rebuild them, don't delete them.

Tested on `Rush\Celeste Room\what happened.mp4` (4K, 97 frames, 24 fps) and its
`_clean_18fps` version.

The dedupe was perfect: 24 held frames at exactly n mod 4 == 3, all removed,
97 -> 73 frames = 18 fps. Nothing wrong with that step.

But the 18 fps result still stutters, and measuring it says why. Judged against
local neighbours, the steps in the cleaned file fall into a hard 3-phase pattern:

    phase 0 (every 3rd step): mean local ratio 1.54   <- the lurch
    phase 1:                  mean local ratio 0.92
    phase 2:                  mean local ratio 0.89

Every third step carries about 1.5x the motion of its neighbours — 24 lurches in
a 4-second clip, roughly 6 per second.

**Why.** The clip was never "18 fps padded up to 24". It is a 24 fps motion
timeline in which every 4th frame was LOST and replaced with a repeat of the one
before it. Deleting the repeat does not restore the missing moment — it leaves a
hole. The remaining frames are then unevenly spaced in time: three close together,
then a double gap, repeating.

So for this class of file, deleting is the wrong move. The frame has to be
painted back in.

## rebuild_held.py  (icon 4)

    python3 _tools/rebuild_held.py FILE [--crf 16] [--dry-run]

Finds the periodic held frames and replaces each one, in place, with an optical
flow midpoint of the two real frames on either side. Frame count, frame rate and
duration all stay exactly as they were. Output: `<name>_rebuilt.mp4`.

Verified on a controlled test: took a clean 121-frame 1080p clip, replaced every
4th frame with a copy of its predecessor, then rebuilt it.

    file                 frozen steps    mean local ratio by phase (mod 4)
    original (untouched)     13          1.04  0.99  1.03  1.07
    every 4th frame held     36          1.97  0.99  1.02  0.05   <- broken
    after rebuild_held       15          1.08  0.99  1.03  1.01   <- restored

The rebuilt clip is statistically indistinguishable from the untouched original.

It can resume: if a run is interrupted, start it again and it picks up from where
it stopped rather than redoing the extraction and the frames it already made.

### Which tool for which symptom

    1-CHECK-VIDEO           says "HELD FRAMES every Nth"   -> use icon 4
    1-CHECK-VIDEO           says "cadence breaks at ..."   -> use icon 3
    both                                                    -> icon 4 first, then icon 3

Icon 2 (`fix_cadence.py --mode clean`) is now the specialist option, not the
default choice: use it only when you specifically want an 18 fps file to retime
by hand in Resolve with Optical Flow. Icon 4 gets you a finished 24 fps file with
no NLE step.

## UPDATE — why rebuilt frames smear, and a recommendation engine in icon 1

Reported on `Rush\After fight\Its funny_rebuilt.mp4`: the invented frames are
visibly soft.

Confirmed, and it is a limitation of the interpolator, not a bug. Measuring the
rebuilt frames against their neighbours block by block (60 px blocks at 960x540):

    11.7% of textured blocks lose more than a quarter of their detail
    worst blocks keep only 56-60% of their detail
    no block loses more than half on average

Visual check at 4K on frame 119 (crops in `After fight\_frames_check\`) shows
exactly what those numbers mean: individual hair strands against a dark background
go to mush in the rebuilt frame while the originals either side are crisp.

**Cause.** ffmpeg's `minterpolate` is a block-matching motion compensator. It
splits the frame into squares and looks for where each square moved. Thin
structures moving fast — hair, grass, rain, fabric weave — do not survive that
model: several plausible matches exist, the estimator hedges, and the result is a
blend.

**Tuning it does not help.** Tested finer blocks and a stronger search
(`mb_size=8:search_param=64:me=umh`) against the current settings on the same frame:

    current settings   mean detail kept 89.5%   worst block 52%   5.7% blocks bad
    mb8 / umh / sp64   mean detail kept 89.1%   worst block 52%   7.5% blocks bad
                       ...and 8x slower

Slightly worse and far slower. There is no setting that fixes this; the model is
wrong for the content.

**What actually works: Topaz Apollo.** `apo-8` is an AI frame interpolator with a
"replace duplicate frames" option — this exact job, done by a model that
understands hair. It is already in the pipeline here: `skip frame test 1.mp4`
carries the tag `Processed using apo-8 replacing duplicate frames`. Run it on the
RAW download, then upscale.

`rebuild_held.py` (icon 4) stays as the free fallback and is genuinely fine on
clips without fine moving detail.

### Icon 1 now recommends a tool, and measures rather than guesses

`cadence_check.py` was rewritten. For any clip with held frames it now runs a
**smear trial**: it actually rebuilds four held frames spread across the clip at
960x540 and measures how much fine detail they lose, then recommends accordingly.

    trial says >4% of textured blocks bad, or worst area under 65%
        -> recommends Topaz Apollo, icon 4 only as fallback
    otherwise
        -> recommends icon 4

Four samples, not one: `Its funny` measured 3.7% on a quiet frame and 7.9% pooled
across the clip. A single sample would have given the wrong advice on the very
file that prompted this.

Output now has three sections per file: WHAT IS WRONG, SMEAR TRIAL, WHAT TO DO.
Runtime is a few seconds for 1080p, about 30 s for a 9-second 4K clip.
`--no-trial` skips the trial if you just want the diagnosis.

## UPDATE — cadence_fix.py: one pass, replaces icons 2, 3 and 4

Repeated frames and missing frames are the same defect seen from two sides, and
fixing one disturbs the evidence the other relies on. Running icon 4 then icon 3
meant two approximations stacked: icon 4 put its invented frame in the repeat's
slot (which is not where the hole in time is), and icon 3 then papered over the
residue by inserting more frames and stretching the clip.

`cadence_fix.py` builds a timing model instead. Each step is classed frozen /
double / normal, which gives every real image an exact position on a motion
timeline. The real images are then laid onto the 24 fps grid where they actually
belong, and only the empty slots are invented.

**The arithmetic that makes it exact.** In a fixed-rate conversion every padded
repeat pays for exactly one lost moment. So once the repeats are counted, the
number of doubled steps is *known* — the tool takes that many largest steps
rather than guessing a threshold. The output lands on the original frame count
by construction, which is why the duration comes out exact. This is also what
killed the old over-detection bug: there is no threshold left to get wrong.

A repeat that is NOT on the periodic phase is genuine stillness — the scene
really did stop — and is kept as a real frame rather than being replaced.

### Measured on `magnific_the-hourglass-castle-the-_SyNxxxZUb8.mp4`

    stage                          frames    dur   phase means         spread
    RAW                                97  4.04s   0.92 1.08 1.68 0.25   1.43
    old: icon 4 then icon 3           106  4.42s   1.15 0.95 1.20 1.07   0.25
    NEW: cadence_fix one pass          97  4.04s   0.99 0.98 0.98 1.01   0.04

1.00 in every phase is perfect. The one-pass result is six times more even than
the two-pass chain AND holds the original duration exactly — no retime, no drift
against the audio.

When a clip has no padded repeats (only missing frames, like `procession
bridge`) there are no spare slots, so the output is genuinely longer. The tool
says so and writes `<name>_cadence_RETIME.txt` with the exact speed to restore
the original length.

Set `CADENCE_TMP` to choose the working directory if the default temp folder is
short of space. Interrupted runs resume: already-invented frames are reused.

### The icons now

    0-SETUP-CHECK.bat       is Python and ffmpeg installed
    1-CHECK-VIDEO.bat       what is wrong + which tool, with a smear trial
    2-FIX-CADENCE.bat       the one-pass repair

    z-old-2-make-18fps-file.bat      kept: strips repeats to an 18 fps file
    z-old-3-fix-dropped-frames.bat   kept: seam-only repair
    z-old-4-rebuild-held-frames.bat  kept: repeat-only repair

The three z-old icons still work and are worth keeping for odd cases, but
2-FIX-CADENCE supersedes all of them.

## UPDATE — never chain the tools; both tools now refuse to

`magnific_audio1-is-provided.-use-i_IfEn7XetvE_rebuilt_cadence.mp4` was made by
running old icon 4 and then new icon 2. Result: 237 frames, 9.875 s, and **61
uneven steps still in it** — the repair achieved nothing.

**Why chaining breaks it.** cadence_fix gets its exact-duration result from one
arithmetic fact: every padded repeat pays for exactly one lost moment, so
counting the repeats tells it how many moments to restore. Icon 4 deletes the
repeats. After that the evidence is gone, cadence_fix falls back to its threshold
path — the very guessing the new design was built to eliminate — and behaves like
the old icon 3: over-detects and pads the clip out. Forced through, it wanted to
make this file 293 frames, 12.208 s, **+23.6%**.

Two guards added, in both icon 1 and icon 2:

1. **By name** — `_rebuilt`, `_seamfix`, `_clean_`, `_cadence`, `_fixed`,
   `_apo8`, `_ganim` in the filename means already processed. Refused.
2. **By content** — no padded repeats but a doubled step more often than every
   10 frames. Real footage does not look like that; a once-repaired clip does.
   Refused.

`--force` overrides both, for the rare case where you know better.

**The rule: icon 2 runs on the original download, once. Never on the output of
anything else, including Topaz.**

## UPDATE — static passages were hiding the padding beat (false refusal)

A freshly downloaded clip, `hf_20260707_191010_c96bbc98...`, was refused as
"already repaired". It was not. 107 of its 289 frames are genuinely still - a
long held pose - and that defeated how the padding beat was being found.

The old method took the gaps between frozen steps and looked for a common
spacing. In a clip with long still passages, frozen steps appear on every phase,
the gaps become mostly 1, and the beat disappears into the noise. With no padding
detected, the tool fell to its threshold path, found 39 "missing moments", wanted
to stretch the clip 13.5%, and the content guard - correctly, on that evidence -
refused it.

**New method: phase coverage.** For each candidate period and phase, ask what
FRACTION of the steps landing there are frozen. Padding shows as one phase near
1.0 against the others:

    P=2: 0.26 0.48                 score +0.22
    P=3: 0.33 0.38 0.41            score +0.05
    P=4: 0.08 0.19 0.44 0.76       score +0.52   <- the beat
    P=5: 0.40 0.36 0.38 0.34 0.37  score +0.04

Accepted when the best phase is at least 0.60 frozen and beats the average of
the others by 0.25. Still passages raise every phase equally, so they cancel out
of the score instead of swamping it.

Result on that clip: 55 padded repeats found, 55 missing moments matched to them,
**289 frames out, 12.042 s, exact original duration** - instead of a refusal.

No regressions: the hourglass clip still resolves at 97 frames exact (beat
confidence 0.97), and the two seam-only clips are unchanged.

`cadence_check.py` uses the same detection, and now separates padded repeats from
genuinely still frames in its report rather than lumping them together.

## UPDATE — padding detection rebuilt: isolated repeats + a drifting beat

Two false accusations in a row, both on genuine original downloads, and both
traced to how the padding beat was being found.

**Case 1** — `hf_20260707_191010_c96bbc98...`, 107 of 289 frames genuinely still.
Gap-based detection collapsed: frozen steps landed on every phase.

**Case 2** — `hf_20260722_214946_a1a07e04...`, 145 frames. Phase-coverage
detection collapsed for the opposite reason: the beat is period 4 throughout,
but the PHASE SLIPS at frame 90.

    frozen step phases mod 4:
      3 3 3 3 3 3 3 3 3 3 3 3 3 3 3 3 3 3 3 3 3 3   (steps 3 - 87)
      2 2 2 2 2 2 2 2 2 2 2 2 2 2                   (steps 90 - 142)

    P=4 phase 3: 21/36 = 0.58   <- just under the 0.60 bar
    P=4 phase 2: 15/36 = 0.42

Every one of the 36 slots is padded. Split across two phases, neither cleared
the threshold, so the tool concluded "no padded repeats" and the content guard
- reasoning correctly from that - called an original file pre-repaired.

**New detection, on two ideas:**

1. **A padded repeat is ISOLATED** - one frozen step with moving steps either
   side. A RUN of consecutive frozen steps is the scene genuinely stopping, and
   is left alone. Inventing motion into a held pose is worse than the stutter.
   This is what survives a one-third-static clip.

2. **Match the beat on a lattice, not a phase.** For each candidate period, ask
   of each isolated repeat: is another one a whole number of periods away,
   nearby? A mid-clip phase slip breaks a global phase test but not a local
   lattice test.

Verified across all four reference clips:

    file                        frames  period  padded  coverage  still  output
    a1a07e04 (phase slip)          145       4      34      0.94      3   145 exact
    c96bbc98 (one third still)     289       4      57      0.79     50   289 exact
    hourglass                       97       4      23      0.96      3    97 exact
    procession bridge              145    none       -         -      2   152 (+4.8%)

All three padded clips resolve to their original length. The seam-only clip is
unchanged. No phase concept remains in either tool.

## UPDATE — the tools were consolidated into one folder

This folder is the single source of truth. The copy under
`Rush\Regeneration\_tools` is stale and carries a MOVED note pointing here.
Everything changed from that point on lands here.

Numbering avoids the existing `3-Convert-Seedance.bat`, which is untouched:

    0-SETUP-CHECK.bat     is Python and ffmpeg installed
    1-CHECK-VIDEO.bat     diagnosis + which tool, with the smear trial
    2-FIX-CADENCE.bat     the one-pass repair
    3-Convert-Seedance    (not mine - left exactly as it was)
    4-CHECK-TIMING.bat    drift check against the source

`cadence_fix.py` here was two bug-fixes behind: it lacked both the local
pairing that stops the picture drifting against the sound and the chain-based
beat detection that handles regionally padded clips. Both are now in.

`cadence_check.py` had a different beat detector from `cadence_fix.py` - a
lattice test rather than chains - so the two could disagree about whether a clip
was padded at all. They now share one detector, lifted from `cadence_fix.py`.

New: `timing_check.py` / icon 4. Measures whether a repaired clip still marches
in step with its source, by comparing cumulative motion rather than matching
frames. Frame matching is useless here - an invented frame equals no source
frame, so the match jumps and reports drift that does not exist. That mistake
produced a false -8 frame reading during testing.

## UPDATE — the regularity gate: not every big step is a missing frame

`hf_20260825_191932_39775d83...` drifted up to 8 frames (0.33 s) against its
audio. It has 217 frames, only 4 frozen steps, and no padding beat - so the tool
fell to the threshold path, "found" 19 missing moments and padded the clip out.

Looking at those 19 detections:

    ratios   1.45 - 1.88, never near 2.0
    spacing  1, 11, 18, 2, 1, 1, 12, 14, 12, 1, 22, 1, 1, 21, 45, 3, 13, 21

A real dropped frame gives a step of about 2.0x its neighbours, and a rate
conversion drops them on a regular beat. This is neither: modest ratios,
clustered in bursts of neighbouring frames with long empty stretches between.
That is fast motion, not a defect. The clip is fine as generated.

Compare a genuine one - `procession bridge`: ratios 1.47 - 2.06, spacing
21, 20, 20, 21, 22, 19.

**Gate added to both tools.** On the no-padding path the detections are only
believed if they look like a rate conversion: median spacing at least 8 frames,
no more than 20% of gaps clustered at 1-2, and at least 60% of gaps within 40%
of the median. Otherwise the clip is left alone.

    file                    breaks  verdict
    39775d83 (this one)         19  irregular -> leave alone
    procession bridge            7  regular   -> repair, +4.8%
    warn pilgrims 2             22  regular   -> repair, +8.3%
    magnific handheld            6  regular   -> repair, +5.0%

This was the last place where the tool guessed. On the padded path the count is
arithmetic; on this path it now has to prove itself first.

**Note on clips with audio and no padding.** When the gate passes, the repair
genuinely must lengthen the clip - there are no wasted slots to reclaim - and
the audio will no longer fit. For those, either accept the original cadence or
retime in Resolve using the RETIME note. There is no free lunch on that path.

## UPDATE — 30-into-24 clips, and why the spacing gate was wrong

`mountain 2.mp4` was dismissed as "nothing to repair". It has a real and very
regular defect: 43 doubled steps whose pattern repeats every 23-24 frames.

    jump spacing: 5,5,5,5,2,2  5,5,5,5,2,2  5,5,5,5,2,2 ...

That is a 30-into-24 pulldown. The shape is lumpy but perfectly periodic, and
the old gate - which demanded evenly spaced gaps - threw it out because 31% of
the gaps were 2s. The gate now tests PERIODICITY instead: is another drop
exactly one cycle later (+/-1 frame)? `mountain 2` scores 92% at a 24-frame
cycle. The irregular clip that started this still scores far below the bar and
is still left alone; `procession bridge` (25-into-24) now passes on a 21-frame
cycle where the spacing test had begun failing it.

The report now names the rate: "43 drops in 217 frames -> the generator ran at
about 29 fps, squeezed into 24 by throwing frames away".

### And the repair for this class changed shape

Restoring dropped frames one at a time only paces correctly if EVERY drop is
found. Miss a few in one stretch and that stretch stays compressed while the
rest expands - which is drift. The first attempt on `mountain 2` produced 260
frames at 28.756 fps, the right duration, and still slid 0.3 s at its worst.

A dropped-frame clip is a rate conversion: a uniform R-fps original sampled down
to 24. The exact inverse is a **uniform resample back to R**, which is evenly
paced by construction. That is now what happens - one pass over the whole clip,
snapped to a standard rate when it is within 6% (mountain 2 -> exactly 30).
Duration unchanged, audio kept, no drift by construction.

The padded path is untouched: there the count is arithmetic, every repeat is
paired locally, and the output stays 24 fps at the original frame count.

    class                          repair                        output
    padded repeats                 pair + repaint in place       24 fps, same count
    dropped frames, periodic       uniform resample to R fps     R fps, same duration
    irregular                      nothing                       untouched

Note the resample is a long single pass - minutes for a 9-second 1080p clip.
