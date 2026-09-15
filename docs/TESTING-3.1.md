# What was measured before 3.1 was released

Three things were tested, in this order, and none of them is the tool grading
its own homework.

---

## 1. The self-test: six known faults, six known answers

`5-SELF-TEST` takes a clip the tool calls `CLEAN`, makes a small copy, breaks
that copy six ways it already knows the answer to, and checks the repairs.

```
  PASS  control     read as CLEAN
  PASS  pad         read as PAD
        period 4, 42 of 42 repeats found
        168f 7.000s unchanged        unevenness 1.29 -> 0.04    re-reads as CLEAN
  PASS  pad_offset  read as PAD
        period 4, 42 of 42 repeats found
        168f 7.000s unchanged        unevenness 1.29 -> 0.04    re-reads as CLEAN
  PASS  seam        read as SEAM
        named [17, 37, 57, 71, 84], wanted [17, 37, 57, 71, 84], matched 5/5
        163f 6.792s unchanged        unevenness 0.28 -> 0.07    re-reads as CLEAN
  PASS  pulldown    read as PULLDOWN
        30.14 fps source, cycle 3 at 1.0
        135f 5.625s unchanged        unevenness 0.81 -> 0.06    re-reads as CLEAN
  PASS  grid        read as GRID
        beat of 2, phases 18% apart (F=9280), and no frame repeated
        166f 6.917s unchanged        unevenness 0.29 -> 0.02    re-reads as CLEAN

  6 of 6 passed
```

`grid` is new in 3.1, and it has to be built differently from the others. Every
other fault **is** whole frames — a copy, a gap — so the case can be made by
shuffling the source frames about. A grid fault is not: every frame is present
and every frame is different, and each one simply sits a fraction of a frame away
from where it belongs. So the pictures have to be made: one interpolated pass at
eight times the rate, then every eighth position picked with a one-eighth wobble
on it. Both phases are picked *off* the original frame positions deliberately —
landing one phase on real frames and the other on invented ones would build a
clip that alternates sharp, soft, sharp, and the test would then be measuring a
texture beat rather than a cadence fault.

---

## 2. Synthetic clips with ground truth, diagnosed by both versions

`tests/make_test_clips.py` builds clips from a smooth, non-repeating texture
panning at a constant speed, with one fault put in on purpose. Because the motion
is constant by construction, anything that measures as uneven is the fault and
nothing else.

Two details in that generator matter, and getting either wrong makes the test
lie about the engine. The texture must not repeat and must have no flat areas —
a generated pattern like `testsrc2` has both, so panning across one produces
frames that genuinely *are* near-copies, and the engine then correctly reports
repeats the test never asked for. And the pan must be sub-pixel and slow — a
whole-pixel pan quantises a 14% wobble in a 3-pixel step down to nothing, while a
fast pan puts consecutive frames past the point where they have anything in
common, where the frame difference stops growing with the distance moved. Both
mistakes look exactly like a clean clip.

| clip | truth | 3.0 says | 3.1 says |
|---|---|---|---|
| `clean` | nothing wrong | CLEAN | CLEAN |
| `fast` | genuine fast motion in bursts | CLEAN | CLEAN |
| `padded` | every 4th frame a copy | PAD | PAD |
| `held` | 3 frozen frames | SEAM | SEAM |
| `pulldown` | every 5th frame dropped | PULLDOWN | PULLDOWN |
| `grid_small` | a 3.5% beat — real, below the floor | CLEAN | CLEAN |
| `pad3`, `pad_drop`, `pd13/15/17` | padding, with and without drops mixed in | PAD | PAD |
| `sp9`, `sp10`, `sp13`, `seam_pull` | drops at various spacings | PULLDOWN | PULLDOWN |
| **`grid`, `grid_fast`** | **a 14% beat, nothing repeated** | **CLEAN** ✗ | **GRID** ✓ |
| **`mixed`** | **padded for 50 frames, clean for 110** | **CLEAN** ✗ | **PAD** ✓ |
| **`sp12`** | **13 isolated drops, implying 26.0 fps — not a rate any generator produces** | **AMBIGUOUS** ✗ (PULLDOWN 0.82 / SEAM 0.77) | **SEAM** ✓ |

Across 22 clips the two versions differ on exactly those four, and every
difference is one of the five bugs. Nothing that 3.0 got right, 3.1 gets wrong.

`sp9` is worth a note in the other direction. Losing a frame every ninth **is**
periodic — nine divides by three, so the phase statistic lights right up — and an
early cut of the grid detector claimed it. It is not a grid fault: frames are
genuinely missing, and redistributing them would smear that damage across the
clip instead of repairing it. `GRID` is the residue class, and it is now claimed
only where there is neither a repeat nor a drop to explain the measurement.

---

## 3. The repairs, measured against the true sequence

Each faulty clip has a *true* version — the clip it would have been without the
fault — and it is the same pan, so the repaired output can be compared against it
frame for frame. This is the test that cannot be fooled by the tool's own
measurements agreeing with themselves.

| clip | PSNR vs the truth, before | after |
|---|---|---|
| `held` (3 frozen frames) | 35.7 dB | **43.1 dB** |
| `padded_lost` (every 4th a copy) | 24.3 dB | **36.6 dB** |
| `grid_fast` (a 14% beat) | 36.5 dB | **43.2 dB** |

And the tool's own measurements on the same runs:

| clip | repainted | unevenness | worst hitch | overall | re-diagnosed |
|---|---|---|---|---|---|
| `held` | 3 of 168 | 0.00 → 0.00 | 1.56× → 1.00× | 0.28 → 0.00 | CLEAN |
| `padded_fast` | 41 of 168 | 1.00 → 0.02 | 1.56× → 1.03× | 1.28 → 0.04 | CLEAN |
| `grid_fast` | 84 of 168 | 0.27 → 0.06 | 1.28× → 1.07× | 0.41 → 0.10 | CLEAN |

(`held` is the case where the two numbers pull apart, and it is worth seeing
why. Three frozen frames in 168 barely move the spread — 165 steps are perfectly
even and three are not — but the worst hitch is the thing you actually see. That
is why the summary score weights it.)

Every output: 168 frames, 7.000s, 24 fps — identical to its source.

### The same `held` clip, under 3.0

```
  PLAN
    even out frames 32-49 (SEAM)
    even out frames 87-104 (SEAM)
    even out frames 122-139 (SEAM)
    48 of 168 frames repainted, 120 copied untouched
    unevenness 0.00 -> 0.25   worst hitch 1.56x -> 2.02x
    re-diagnosed as: SEAM   ** still reads as broken **
    overall 0.28 -> 0.76   ** NO BETTER - do not use this file, keep the original **
  done: held30_even.mp4
  exit code 0
```

Forty-eight frames repainted to fix three frozen ones; the clip came out
measurably *worse* than it went in; the engine said so in capitals — and then
wrote the file under the same name a good repair gets and told the launcher
everything was fine. Bugs 2 and 5, in one run.

The same clip under 3.1: three frames repainted, worst hitch 1.56× → 1.00×,
re-diagnosed CLEAN.

---

## What this does not tell you

It does not tell you the first-try accuracy on real footage. That number comes
from rounds of real clips measured independently before the tool sees them —
70% for 3.0 across 60 clips — and the next round of that is what will say
whether 3.1 clears the 80% bar. Of the 18 clips 3.0 read wrong, 14 are accounted
for by bugs 1, 3 and 4 (6 + 1 + 7), and bug 2 accounts for four repairs that were
diagnosed correctly and then botched. But "accounted for" and "fixed in the wild"
are different claims, and only the next round will settle it.

It also does not find new kinds of damage. Only a real clip can do that.
