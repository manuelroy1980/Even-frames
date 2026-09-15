# Even Frames in Resolve

Repairs the clips on a timeline track and stacks each repair on the track
directly above its original, so you can toggle the top track to compare.

**Requires DaVinci Resolve Studio.** The free version does not include the
scripting API. Tested against Resolve Studio 21.1, on Windows and with the
engine at version 3.1.

## Why this is short

The repair guarantees the file it writes has the same frame count, the same
frame rate and the same duration as the file it read. So a repaired file is a
drop-in: the same source in and out points land on the same pictures, and the
repaired clip goes at the identical record position with nothing to retime and
nothing to conform. All the hard parts were paid for upstream.

## Running it on a Mac

Use `EVEN-FRAMES-IN-RESOLVE.command` instead of the `.bat`. It is the same
script; only the two paths to Resolve's scripting module differ, and they are at
the top of the file if your install is somewhere unusual. macOS will refuse to
open it on the first double-click — right-click, Open, then Open again. See
`READ-ME-FIRST-MAC.txt` in the folder above.

## Before you run it

1. Resolve open, with the timeline you want showing.
2. **Preferences > System > General > External scripting using: Local.**
3. The clips on the track are real media files, not compound or Fusion clips.

## Running it

Double-click `EVEN-FRAMES-IN-RESOLVE.bat`. It reads V1 and writes to V2.

Start with a dry run - it diagnoses every clip and prints what it would do,
without changing anything or writing a file:

    EVEN-FRAMES-IN-RESOLVE.bat --dry-run

Then:

    EVEN-FRAMES-IN-RESOLVE.bat                      V1 -> V2
    EVEN-FRAMES-IN-RESOLVE.bat --track 2            V2 -> V3
    EVEN-FRAMES-IN-RESOLVE.bat --timeline "Timeline 24"
    EVEN-FRAMES-IN-RESOLVE.bat --only 1,4,7         just those, by the printed number
    EVEN-FRAMES-IN-RESOLVE.bat --name "repaired"    name the destination track
    EVEN-FRAMES-IN-RESOLVE.bat --name ""            leave the track name alone

## What it will not do

- Touch the source track. Ever.
- Write onto a destination track that already has clips in the way - it stops
  and says so instead.
- Place a repair the tool graded `NO BETTER`, or one whose frame count or
  duration came back different. Those are reported and left out. From 3.1 this
  is airtight: a condemned repair is written as `_even_REJECTED.mp4` and the
  repair exits with an error, so there is no longer any way for one to reach
  the timeline.
- Repair the same file twice. A `_even.mp4` newer than its source is reused.

## Notes

- The destination track is renamed `fix` (change it with `--name`).
- Repaired clips are placed **video only**. The original underneath keeps the
  sound, so nothing is doubled.
- Repaired files are written beside their originals as `<name>_even.mp4`, and
  imported into a media pool bin called **Even frames**.
- A clip cut into the timeline more than once is repaired once and placed as
  many times as it appears.
- It runs outside Resolve on purpose. A repair takes minutes per clip; run from
  Resolve's own console that means a frozen application with no progress bar.
