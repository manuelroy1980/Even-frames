# Even Frames

**Repairs the once-a-second stutter in AI-generated video. Windows. Free. No coding.**

If you generate video with Seedance, Kling, Veo, Hailuo, Runway or similar, you have
probably seen it: the motion hiccups roughly every second. It survives editing, it
survives upscaling, and it is the single thing that makes an otherwise good shot look
generated.

Drag your clip onto `1-FIX-VIDEO.bat`. You get `yourclip_even.mp4` back, beside the
original, and a short note saying what was done.

---

## The rule this is built on

> **The output is always 24 fps, always the same number of frames, and always exactly
> as long as the input.**

So the repaired file drops straight onto a 24 fps timeline next to its audio. Nothing to
retime, nothing to conform, no speed percentage to type into your editor.

If a clip can only be fixed by making it longer, the tool says so and **leaves the file
alone**. That is deliberate. A tool that hands you something that looks fixed and is not
is worse than no tool.

---

## Three faults that look identical

They all read as the same hiccup, and they need three different repairs. Applying the
wrong one makes the clip worse — which is the whole reason this exists.

| | What actually happened |
|---|---|
| **Held frames** | Every 4th picture is a copy of the one before it. The real frame was thrown away. |
| **Missing frames** | A few pictures are simply gone. Nothing repeats — there is just a lurch. |
| **Dropped throughout** | Made at 25 or 30 fps and squeezed into 24 by discarding frames all the way along. |

`1-FIX-VIDEO` diagnoses first, prints the evidence it used, then applies the one repair
that fits. If two explanations fit about equally well it says **AMBIGUOUS** and stops
rather than guessing.

---

## It checks its own work

Every run re-measures the file it just wrote and diagnoses it again from scratch:

```
frames 97 -> 97, 4.042s -> 4.042s, 24 fps -> 24 fps  OK
unevenness 1.04 -> 0.08   worst hitch 2.35x -> 1.26x
re-diagnosed as: CLEAN   (the defect is gone)
overall 1.24 -> 0.12   evened out
```

If it did not actually improve the clip it prints `** NO BETTER - do not use this file **`
and tells you to keep your original. It is not allowed to claim success without measuring.

---

## Install

Two free programs, once, about five minutes. Open a Command Prompt (Windows key, type
`cmd`, Enter) and run:

```
winget install Gyan.FFmpeg
winget install Python.Python.3.12
```

Close that window, open a **new** one, then double-click `0-SETUP-CHECK.bat`. You want
"Python OK" and "ffmpeg OK".

Full walkthrough with screenshots of what to expect: **[START-HERE.txt](START-HERE.txt)**.

---

## The buttons

| | |
|---|---|
| `0-SETUP-CHECK.bat` | Is Python and ffmpeg installed |
| `1-FIX-VIDEO.bat` | **Drag clips here.** Diagnose, then repair |
| `2-DIAGNOSE-ONLY.bat` | Same diagnosis, writes nothing |
| `4-CHECK-TIMING.bat` | Does the repaired clip still sit where the original did |
| `5-SELF-TEST.bat` | Breaks a copy of a clean clip four known ways and checks the repairs still work |

You can drop many files at once.

---

## Use the raw download

Not a Topaz file, not a file you have already run this on. The repair works by reading
the original damage to work out what happened, and an earlier pass has erased it. The
tool refuses when it spots this, but start clean.

---

## Documentation

- **[START-HERE.txt](START-HERE.txt)** — the plain-English manual. Start here.
- **[docs/HOW-IT-WORKS.md](docs/HOW-IT-WORKS.md)** — the detector, the repairs, and the proof.
- **[docs/WHY-IT-HAPPENS.md](docs/WHY-IT-HAPPENS.md)** — measurements on 60 raw generations, and why the damage exists at all.
- **[docs/BUILD-HISTORY.md](docs/BUILD-HISTORY.md)** — the build log, including what was got wrong along the way.

---

## Requirements

Windows · Python 3.9+ · ffmpeg. No pip install, no dependencies — the Python here uses
only the standard library and calls ffmpeg.

---

## Contributing

Bug reports are genuinely useful, especially a clip that gets diagnosed wrong. Open an
issue with the whole `DIAGNOSIS` block the tool printed. If you can share the clip, even
better.

---

## Licence

MIT — see [LICENSE](LICENSE). Use it, change it, ship it in something commercial. Just
keep the copyright line.
