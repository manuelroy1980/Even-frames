# Changelog

## 3.1 — 2026-09-14

Five fixes, all of them found by testing the tool against ground truth on sixty
real clips laid out in four rounds. Every clip was measured independently
*before* the tool saw it; picking test clips by asking the tool what looked
broken would have guaranteed a near-perfect score and told us nothing.

Version 3.0 diagnosed 42 of those 60 correctly on the first try — 70%, and
stable at exactly 14/20 across three independent draws, so it was the real
number and not a bad run. The five things below are where the other 18 went.

### 1. The ambiguity guard no longer refuses on evidence that is not in conflict

When two class scores both came back high, the engine declared `AMBIGUOUS` and
did nothing. The intent was right — never guess between two readings — but it
fired on pairs that were not competing.

`PAD`, `PULLDOWN` and `SEAM` are not independent hypotheses. A padded clip **is**
a rate conversion; the repeats are how that converter did it. So `PULLDOWN`
scores high on every padded clip by construction, and the gap between the two
scores carries no information at all.

Six clips in sixty were refused on pairs like `PAD 1.00 / PULLDOWN 0.89` and
`SEAM 1.00 / PULLDOWN 0.98`. All six repaired correctly the moment the class was
named by hand.

The gate is now on the winner's **own** evidence — repeats on a solid beat,
isolated doubled steps well separated, an implied source rate that lands on a
rate a generator actually produces. The runner-up's score is used only to break a
tie where neither class has evidence of its own, which is what `AMBIGUOUS` was
always meant to mean.

### 2. The SEAM repair now targets the held frames it was given

A held frame and a dropped frame look the same from a distance and are not the
same thing. When a generator freezes a frame it has to lose a real one to keep
the count, so the damage comes in a **pair**: a repeat sitting next to a step
carrying two frames of motion. The old repair treated every hitch as a bare drop
— it found a single held frame at position 70 of 241 unaided, repainted 57
frames around it, and left the held frame exactly where it was. On another clip
it repainted 104 of 145 frames and both holds survived. All of them reported
success.

Now the repeat is recognised as a wasted slot, exactly like a padded one, and is
repainted in place: **one frame per hitch**, and the freeze actually goes. Where
nothing was repeated there is no slot to paint into, so the lurch is spread over
a short window instead — and the default window is 4 frames either side, not 8.

There is also a hard ceiling on how much of a clip a repair may repaint (25% for
`SEAM`, 60% for `PAD`). Past that the plan is wrong, and the tool stops before
writing anything rather than after.

### 3. A region-level finding is no longer outvoted at clip level

The clip verdict was the class covering the most frames, and nothing else. On a
clip split into three regions where the first was padded and the other two were
clean, the whole-clip verdict came back `CLEAN` and nothing was repaired — while
the log row underneath read `regions=PAD|CLEAN|CLEAN, rep=8, cov=1.00`. The
engine had found the fault, written it down, and then outvoted itself.

One region with real evidence now makes the clip repairable. This costs nothing:
the repair was always planned region by region, so the clean stretches are copied
through untouched either way.

### 4. New: a detector for the periodic grid

The largest untouched class in the library — roughly sixty clips in four hundred
— and every earlier version called them `CLEAN`.

No frame is repeated and no step is doubled, so neither detector sees anything.
What is there instead is an alternation: long step, short step, long step, in
strict phase across the whole frame, usually 6 to 18%. It is what you get when a
render at one rate is resampled onto another by an encoder that blends rather
than drops.

The test is a one-way ANOVA on the step sizes grouped by index modulo 2, 3 and 4.
Real motion has no opinion about whether a frame's index is even or odd; a
resampled clip has a very strong one. The floor is 8% — below that it is not
worth repainting a frame over, and the tool leaves it alone.

The repair moves every frame back onto an even grid. Measured against ground
truth on a test clip: the alternation went from 24% to 5%, and the repaired clip
came out 6.8 dB closer to the true even sequence.

### 5. Failures and condemned files are no longer reported as success

Three separate reporting faults, all of them cheap, all of them corrosive.

- **The ghosting check worked, and then the file was written anyway.** It caught
  genuine double images in four clips and printed `DO NOT USE THIS FILE` in
  capitals — and then wrote them as `<name>_even.mp4`, the same name a good
  repair gets, indistinguishable on disk a week later. Condemned files are now
  named `<name>_even_REJECTED.mp4`. They are kept rather than deleted, because
  they are evidence, but nothing will mistake one for a repair.
- **`cadence_fix.py` exited 0 after printing FAILED.** A ten-clip batch with two
  crashes in it finished `ALL DONE · Problems: 0` and told you the repairs were
  saved. The exit code now means something, and the launcher reports it.
- **The scratch folder was shared.** Two runs at once destroyed each other's
  frames, silently, and both produced nonsense. Each input now gets its own
  scratch folder, derived from its path — so re-running the same clip still finds
  the frames it extracted last time, which is the one thing the shared folder was
  good for.

### Also in 3.1

- **`resolve/` ships for the first time.** A companion script for DaVinci
  Resolve **Studio** that repairs a whole video track and stacks each repair on
  the track above its original. It has existed for a while and was never
  released. Bug 5 makes it materially safer: it decides what to place by the
  repair's exit code, which until now was always 0, so a condemned repair could
  reach the timeline. It cannot any more.
- **macOS support.** The same engine, with `.command` launchers and a setup check
  that knows about Homebrew. Nothing in the engine was Windows-specific; the
  launchers were.
- The self-test now has six cases instead of five — `grid` is new — and builds
  its grid case by interpolating the source clip at eight times the rate, because
  a sub-frame fault cannot be made by shuffling whole frames about.
- The engine's own reasoning about a close call (`why`) is now printed in the
  diagnosis. It was being collected and never shown.
- Four new columns in `cadence-log.tsv`: `grid_m`, `grid_amp`, `grid_F`,
  `evidence`. Existing logs are widened in place, so old rows still line up.
- `--force-class GRID` is accepted alongside `PAD`, `SEAM` and `PULLDOWN`.

### Unchanged, deliberately

The frame-count contract: 50 repairs across the four test rounds, every one of
them 24 fps with the source's frame count and duration to the millisecond. Never
broken, and nothing in this release goes near it.

Across those same 60 clips the tool never once applied a confident repair to a
wrong reading. Every fix in this release should make it act **more often**. None
of them should make it act less carefully.

## 3.0 — 2026-09-04

One engine. Both buttons import `cadence_core` and read the same verdict, so the
checker and the repairer can no longer tell you two different stories about the
same clip — which is what produced the "right tool, wrong problem" results that
started the rebuild. Region-by-region classification, evidence scoring with a
margin, the self-test, and the run log all arrived here.
