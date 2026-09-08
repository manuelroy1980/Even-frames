# Why the cadence artifacts exist — research addendum, 2026-08-30

Companion to `README.md`. That file records **what** the artifacts are and how the
tools repair them. This one records **why** they happen, what the literature says,
and three new measurements that change how the pipeline should be run.



---

## 1. Artifact A confirmed on raw, untouched downloads

Profiled 60 raw files in `Rush/` with no `videoai` tag (never through Topaz).

On the padded ones every stalled step lands on `n mod 4 == 3` with **phase purity
1.00** — no exceptions, no phase slip on these particular files. The per-frame
signature is a repeating group of four:

    [ move · move · HOLD · double-move ]

`hf_20260808_171801` (97 frames, median step 6.12):

    3.16 3.21 0.17 6.47 | 4.30 5.18 0.18 11.06 | 7.46 7.68 0.14 13.34 | ...

The 0.17 is the held frame. The 6.47 / 11.06 / 13.34 is the catch-up — two
frames' worth of motion crossed in one step. 24 held frames in 97 leaves 73 real
frames over 4.04 s = **18.06 fps of real motion in a 24 fps container**.

This is a nearest-neighbour frame-rate conversion in the delivery layer, not a
diffusion defect. Same arithmetic as 3:2 pulldown. Confirms the README's
Artifact A diagnosis on files Topaz never touched.

---

## 2. NEW — the muxer tag predicts whether a file is padded

Every download carries the libavformat version that wrote it. Sorting the 60 raw
files by it splits them almost perfectly:

    Lavf58.76.100    20 files     8 padded  (40%)
    Lavf60.16.100    36 files     0 padded  ( 0%)
    Lavf62.13.100     4 files     1 padded   (Topaz output, inherits its input)

`hf_` files appear in BOTH groups, some from the same day. So this is not
"Higgsfield is bad" — Higgsfield routes different models or tiers through
different backends, and one of them resamples on the way out. The FFmpeg 4.4-era
muxer is the fingerprint of that path. The one Magnific file in the sample is
also Lavf58.76 and also padded.

Screen every download in one second:

    ffprobe -v error -show_entries format_tags=encoder -of default=nw=1:nk=1 clip.mp4

    Lavf58.76.100  -> run 1-CHECK-VIDEO / 2-FIX-CADENCE before anything else
    Lavf60.16.100  -> very likely clean; still worth a check on fast action

**Next experiment, highest value:** generate the same prompt on each Higgsfield
tier you use, download all of them, ffprobe the tags. If the padded path maps to
a specific tier or resolution option, avoiding it kills the problem at source
instead of repairing 20-40% of footage forever.

---

## 3. NEW — Topaz grain makes held frames invisible to duplicate detection

Four clips tagged `ganim-1; add noise at 2; grain strength 20` measure **zero**
exact duplicate frames under `mpdecimate`, and yet every one has a perfectly
phase-pure period-4 stall pattern:

    nobody actually knows      169 f   42 held   0 exact dups
    exiting the forest          97 f   24 held   0 exact dups
    father running             145 f   36 held   0 exact dups
    Meeno walking the path      97 f   24 held   0 exact dups

Gaussian grain is generated per frame. Two identical frames get two different
grain fields and stop being byte-identical. mpdecimate stops seeing them, Topaz
Apollo's "replace duplicate frames" stops seeing them, and any padded-repeat
count reads zero. The stutter is fully intact; only the evidence is gone.

**Suggested guard for `cadence_check.py` / `cadence_fix.py`:** if the container's
`videoai` tag contains "grain", do not trust the duplicate-based path. Fall
through to the motion-profile detector — the phase-purity test on stalled steps
still fires at 1.00 on these files, so the diagnosis is recoverable. Print the
reason, naming grain.

This is the sharpest possible argument for the existing rule: repair the raw
download, once, before Topaz.

---

## 4. Why the model's native rate isn't 24

- **The 4n+1 frame counts** (97, 121, 145, 169, 193, 241) come from the causal 3D
  VAE every current model uses. Hunyuan's public frame documentation states it:
  the default "884" VAE applies 4:1 temporal compression and requires frame counts
  matching `(frames - 1) // 4 + 1`, because the first frame is encoded and decoded
  differently from the rest. One latent frame decodes to four output frames — and
  four is the period we measure.
- **The four frames in a group are not reconstructed equally.** Wu et al.,
  *Improved Video VAE* (CVPR 2025): "the front frame cannot interact with other
  frames in the group due to the forward flow of causal convolution, while the last
  frame can acquire the information of the whole frame group"; such methods "suffer
  from significant inter-frame flicker" and "usually perform poorly in the first two
  frames of a frame group". Their Group Causal Convolution exists to fix exactly
  this. That is the residual wobble in clips with no padding at all — small, and not
  repairable downstream.
- **Fast/Turbo tiers are distilled, and distillation kills motion.** *Adaptive
  Video Distillation* (arXiv 2603.21864): "mode collapse extends into the temporal
  dimension, resulting in videos with limited or even static motion". This shows up
  as genuinely still passages rather than a periodic beat — which is precisely the
  false-refusal case the detector was rebuilt around.
- **Chunk seams to expect from 2.5.** *Progressive Autoregressive Video Diffusion
  Models* documents "frame-to-frame discontinuity" at chunk boundaries, "temporal
  jittering", and a measurable "reduction in dynamic degree" over a long clip.
  Seedance 2.5 does 30 s in one pass "with multi-round extensions" — extensions are
  chunks. Expect an isolated hitch at the join, not a periodic beat. seam_fix
  handles that shape already.

---

## 5. Seedance 2.0 vs 2.5, as it bears on this

|  | 2.0 | 2.5 |
|---|---|---|
| Duration | 4-15 s single pass | 4-30 s single pass + multi-round extensions |
| Resolution | 480p / 720p / 1080p / 4K | 480p / 720p only |
| Frame rate | not published; measured 24 fps container, 18 fps real motion on the padded path | not published |
| References | 12 files (9 img / 3 vid / 3 audio) | 50 files (30 img / 10 vid / 10 audio) |
| Price @720p | ~$0.30 / s | ~$0.47 / s |
| Fast motion | independent test: "presents clearer key poses more often" | same test: robe, limbs, weapon edges "soften" in dense combat |

ByteDance's own 2.5 caveat: remaining weakness in "the physical plausibility of
complex motions and the stability of scenes involving interactions among multiple
subjects" — which is the pyramid brawl exactly.

Reading: 2.5 is a length and continuity upgrade, not a motion-smoothness upgrade.
Nothing published suggests the delivery pipeline changed. **Measure a 2.5 file the
day you get one** — same ffprobe, same cadence_check — rather than assuming.

---

## 6. State of the public discussion

There is **no engineering write-up from any video-model team** on cadence
artifacts, no known-issues page, and no published output frame rate on any
platform, API doc or model card. The mechanism is documented only in VAE and
distillation papers that never name a product; the creator-side account is Dr.
Dreams' X post (16 fps / every 3rd frame) plus tool-forum folklore. Nobody has
joined the two. Our 18 fps / every 4th measurement is the same mechanism at a
different ratio, which suggests native rate varies by tier.

No YouTube channel covers it. AI-filmmaking channels do prompting and model
comparisons; post-production channels do timeline conform. Open lane.

## Sources

- Wu et al., *Improved Video VAE for Latent Video Diffusion Model*, CVPR 2025 — https://arxiv.org/html/2411.06449v1
- *Adaptive Video Distillation*, arXiv 2603.21864 — https://arxiv.org/html/2603.21864v1
- *Progressive Autoregressive Video Diffusion Models* — https://arxiv.org/html/2410.08151v2
- *When Distillation Breaks Motion Control*, arXiv 2506.19348 — https://arxiv.org/html/2506.19348v2
- *LeanVAE*, ICCV 2025 — https://arxiv.org/html/2503.14325v1
- Hunyuan-GameCraft, how-frames-work.md (the 4n+1 rule) — https://huggingface.co/spaces/OrangyDev/Hunyuan-GameCraft/blob/main/how-frames-work.md
- ByteDance Seed, Seedance 2.5 announcement — https://seed.bytedance.com/en/blog/one-take-creation-flexible-referencing-introducing-seedance-2-5
- ByteDance Seed, Seedance 1.0 tech report — https://seed.bytedance.com/en/blog/tech-report-of-seedance-1-0-is-now-publicly-available
- Seedance 2.0 paper, arXiv 2604.14148 — https://arxiv.org/abs/2604.14148
- fal, Seedance 2.5 vs 2.0 — https://fal.ai/learn/devs/seedance-2-5-vs-seedance-2-0
- Clipdance, 2.0 vs 2.5 fighting test — https://clipdance.ai/blog/seedance-2-vs-seedance-2-5-fighting-test
- HackerNoon, frame-rate mismatch — https://hackernoon.com/frame-rate-mismatch-is-why-your-ai-video-judders-runway-24fps-vs-kling-60fps
- NemoVideo, Seedance 2.0 quality fixes — https://www.nemovideo.com/blog/seedance-2-video-quality-fix
- Higgsfield, Seedance 2.0 product page — https://higgsfield.ai/seedance/2.0
- Rife-App, frame de-duplication thread — https://itch.io/t/1938540/frame-de-duplication
- Dr. Dreams / Ryan Lightbourn on X — already logged in README.md

---

# CORRECTION 2026-08-30 (later) — the muxer tag does NOT predict padding

Section 2 above is wrong. Tested against the purpose-built folder
`Muxer tag analysis\` (13 clips).

**All 13 carry the identical tag `Lavf58.76.100`.** 3 are padded, 10 are clean.
The tag discriminates nothing.

    file                              codec        size        frm  result
    Seedance 2.0 fast -2.mp4          avc1 8-bit   1280x720     97  PADDED 24 held, 18.1 fps real
    hf_20260825_185902_ac6fafd9...    avc1 8-bit   1920x1080   169  PADDED 42 held, 18.0 fps real
    magnific_magnific_Seedance 2 -3   avc1 8-bit   1920x1080    97  PADDED 24 held, 18.1 fps real
    Seedance 2.0 fast.mp4             avc1 8-bit   1280x720    169  clean
    Seedance 2.mp4                    avc1 8-bit   1920x1080   121  clean
    h3.mp4  (MiniMax)                 avc1 8-bit   2560x1440   124  clean  <- NOT 24n+1
    magnific_Seedance 2.mp4           avc1 8-bit   1920x1080   145  clean
    magnific_Seedance 2 -2.mp4        avc1 8-bit   1920x1080    97  clean
    hf_20260825_234721_5a44a1cb...    hvc1 10-bit  1920x1080    97  clean
    hf_20260826_175519_1c8d5333...    hvc1 10-bit  1920x1080   193  clean, 0 stalls
    hf_20260826_181014_0bb4545c...    hvc1 10-bit  1920x1080   121  clean, 0 stalls
    hf_20260828_152232_c5329874...    hvc1 10-bit  1920x1080   145  clean, 0 stalls
    hf_20260829_145232_37e9cca7...    hvc1 10-bit  1920x1080    97  clean

## What the tag was really tracking: DATE

The Lavf60.16 files in the first sample all came from two clean days, so the
correlation looked causal and wasn't. Re-tested across ALL 39 unique raw `hf_`
downloads project-wide, ordered by the timestamp in the filename:

    2026-07-12 01:23  avc1  145 f  PADDED  coverage 1.00  18.0 fps real
    2026-08-04 00:36  hvc1  241 f  PADDED  coverage 0.58  20.5 fps real
    2026-08-04 00:56  hvc1   97 f  PADDED  coverage 0.83  19.1 fps real
    2026-08-08 12:28  avc1  217 f  PADDED  coverage 0.96  18.2 fps real
    2026-08-08 14:49  avc1  145 f  PADDED  coverage 1.00  18.0 fps real
    2026-08-08 17:18  avc1   97 f  PADDED  coverage 1.00  18.1 fps real
    2026-08-13 16:13  avc1  193 f  PADDED  coverage 0.48  21.1 fps real
    2026-08-25 18:59  avc1  169 f  PADDED  coverage 1.00  18.0 fps real
    --------------------------------------------------------------- cutoff
    2026-08-25 23:47  hvc1   97 f  clean
    ... 16 more, ALL clean, through 2026-08-29 14:52

**Every padded file predates 2026-08-25 18:59. The 17 downloads since are clean.**

*Coverage* = fraction of available slots on the padded phase that actually carry a
held frame. 1.00 = a clean 18 fps conversion. 0.48 / 0.58 = intermittent beat or a
phase slip mid-clip, which is why effective rate comes out at an odd 20.5 or 21.1.

## Secondary correlate: codec

    hvc1 / Main 10 / 10-bit / bt709 / brand isomiso2mp41      12 of 14 clean
                                                              (11 of 11 since 25 Aug)
    avc1 / High / 8-bit / no colour tags / isomiso2avc1mp41    6 of 25 padded

Suggestive, not decisive — the two Aug 4 padded files ARE hvc1. Codec is a
correlate of which delivery path served the file, not the cause of the padding.

## Detection method used here (better than the mod-4 phase count)

A padded repeat is an **isolated** frozen step: one stalled frame with moving
frames on both sides. A **run** of consecutive frozen steps is the scene genuinely
stopping and must be left alone. Score = phase purity x slot coverage over
isolated stalls only; accept above 0.45. This is the same idea already in
`cadence_fix.py`, and it cleanly separated all 13 files (padded ones score 1.00,
clean ones score 0.00-0.15) where a plain phase-count did not.

## Revised advice

1. **There is no metadata shortcut.** Measuring is the only reliable screen.
   `cadence_check.py` already is that screen; nothing replaces it.
2. **Sweep the backlog by date.** Everything generated before 2026-08-25 18:59
   needs checking. Everything since probably does not.
3. **Establish whether 25 Aug was a platform fix or a habit change.** Seventeen
   clean downloads is either Higgsfield having repaired its resampler, or you
   having settled on a different generation option that evening. Regenerate one
   test on whatever setting produced `hf_20260825_185902` and measure it. If it
   comes back padded, the setting is the cause and can simply be avoided. If it
   comes back clean, the platform fixed it and the issue is behind you.
4. **Prefer the HEVC 1080p10 delivery** where the option exists — best record in
   the sample, though not a guarantee.

Note: MiniMax H3 delivered 124 frames, which is NOT 24n+1. Different frame
arithmetic from Seedance entirely — worth its own check if it enters the pipeline.

---

# CORRECTION 2 — Seedance is NOT an 18 fps model, and the folder needs re-scoring

Two things wrong in everything above, both caught by Manu.

## A. "18 fps padded up to 24" is the wrong description

All 13 files in `Muxer tag analysis\` are **exactly 24.000 fps** (`r_frame_rate
24/1`), constant frame rate, durations exactly N + 0.0417 s. And **8 of the 13
have zero held frames and zero lost frames** - 24 fps declared AND 24 fps of real
motion.

If Seedance generated at 18 fps, every file would be padded. They are not.

The correct statement - which README.md already reached on 2026-08-26 under
"held frames are LOST frames" and which this document had regressed on:

> A padded file is a **24 fps motion timeline in which every 4th frame was lost**
> and replaced with a copy of the one before it. It PLAYS as 18 distinct images
> per second. It is a loss with a cover-up, not a frame-rate conversion.

This matters for repair, exactly as README already says: deleting the copies does
not restore the missing moments, it leaves the survivors unevenly spaced. The
frame must be painted back into its own slot. `cadence_fix.py` already does the
right thing; only the explanation here was wrong.

The 4-frame period is still explained by the causal 3D VAE (one latent frame ->
four output frames, `4n+1` counts). Whatever fails, it fails once per latent
group. The community's 16 fps / every-3rd-frame reports are the same failure at a
different group size.

## B. Settings do not determine it - it is per-generation

`hf_20260825_185902` was generated as **Seedance 2, 1080p, High bitrate,
1920x1080** - the same settings as clean files in the same folder. Identical
settings, opposite outcomes.

So there is no setting to avoid and no dialog that prevents it. It fails
server-side on some generations and not others, at roughly a 1-in-5 base rate over
the affected period. **Measuring every download is the only defence.** Revise
advice item 3 in the previous correction accordingly - a single regeneration test
cannot answer the question; only a run of them could.

## C. The folder re-scored for BOTH artifacts

The previous table only tested for Artifact A (held frames). Re-tested for
Artifact B (lost frames) as well, using a rolling local median (window 16) with
hold < 0.30x local and double > 1.60x local, excluding the catch-up step that
naturally follows every held frame:

    file                            frm  A:held  B:lost  beat   verdict
    Seedance 2.0 fast -2.mp4         97      24       0     -   A: 24 held, phase 3 -> 18.1 fps real
    magnific_magnific_Seedance 2 -3  97      24       0     -   A: 24 held, phase 3 -> 18.1 fps real
    hf_20260825_185902_ac6fafd9...  169      42      13    12   A + B, the worst in the set
    magnific_Seedance 2.mp4         145       0       8    18   B ONLY - ~25.3 fps source decimated
    Seedance 2.0 fast.mp4           169       0       4     -   clean (4 marginal steps, no beat)
    Seedance 2.mp4                  121       0       2     -   clean
    h3.mp4  (MiniMax)               124       0       0     -   clean
    magnific_Seedance 2 -2.mp4       97       0       0     -   clean
    hf_20260825_234721_5a44a1cb...   97       2       7     -   clean (very low motion, marginal)
    hf_20260826_175519_1c8d5333...  193       0       0     -   clean
    hf_20260826_181014_0bb4545c...  121       0       2     -   clean
    hf_20260828_152232_c5329874...  145       0       0     -   clean
    hf_20260829_145232_37e9cca7...   97       0       2     -   clean

**`magnific_Seedance 2.mp4` was wrongly called clean in the previous correction.**
It has Artifact B: 8 lost frames on a ~18-frame beat (spacings 18, 15, 19, 15, 18),
implying a ~25.3 fps source decimated to 24. Run icon 2 on it.

So: 3 padded, 1 dropped-only, 1 with both, 8 clean. Out of 13.

## D. A note on cadence_check's reading of `Seedance 2.0 fast.mp4`

`cadence_check.py` reports **17 cadence breaks** on this file and then warns
"almost certainly been repaired once already - go back to the ORIGINAL download".

It is an original download. Measured against a rolling local median instead, there
are only **4** doubled steps (58, 82, 98, 154), all mild at 1.60-1.78x local, with
irregular spacing (24, 16, 56) and no periodic beat. ffmpeg scene detection finds
no hard cuts, so these are not shot changes - most likely ordinary acceleration.

Two suggested tweaks:
1. The break threshold is catching normal motion. Requiring a ratio above ~1.6x
   the LOCAL median (window ~16) and a consistent spacing before calling it a beat
   would drop this from 17 to 0-4.
2. The "already repaired" content guard fires on any original with no padded
   repeats plus frequent breaks. That is the third false accusation of its kind.
   Consider requiring a detectable periodic beat, not just a break density, before
   refusing - and always print the measured spacings so the accusation is auditable.
