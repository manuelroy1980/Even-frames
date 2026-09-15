#!/usr/bin/env python3
"""Diagnose every clip in a folder and print one line each.

  python3 diagnose_all.py [folder] [path/to/the/engine]

The second argument lets you point it at a different copy of the engine, which
is how two versions get compared on the same clips:

  python3 diagnose_all.py clips ../../cadence-tools-3.0
  python3 diagnose_all.py clips ..

Writes nothing. Reads only.
"""
import os, sys

folder = sys.argv[1] if len(sys.argv) > 1 else "clips"
engine = sys.argv[2] if len(sys.argv) > 2 else os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, engine)
import cadence_core as core

print(f"--- engine {core.VERSION} from {engine}")
for f in sorted(os.listdir(folder)):
    if not f.lower().endswith((".mp4", ".mov", ".mkv", ".webm")):
        continue
    try:
        v = core.diagnose(os.path.join(folder, f), force=True)
    except Exception as e:
        print(f"{f:22} ERROR {type(e).__name__}: {e}")
        continue
    best = {}
    for r in v.regions:
        for k, x in r["scores"].items():
            best[k] = max(best.get(k, 0), x)
    regions = "|".join(r["klass"] for r in v.regions)
    print(f"{f:22} {v.klass:10} regions={regions:26} " +
          " ".join(f"{k}={x:.2f}" for k, x in
                   sorted(best.items(), key=lambda kv: -kv[1]) if x > 0))
