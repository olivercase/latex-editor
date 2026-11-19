#!/usr/bin/env python3
"""Checks that the editor cannot damage a manuscript. Reads the papers,
writes nothing into them.

    python3 test_roundtrip.py               papers next to the editor (-v lists locked blocks)
    python3 test_roundtrip.py --fixture     only the bundled example paper
    python3 test_roundtrip.py --root DIR    papers in DIR
"""
import sys
from collections import Counter
from pathlib import Path

import server as S

verbose = "-v" in sys.argv
if "--fixture" in sys.argv:
    S.ROOT = Path(__file__).resolve().parent / "tests" / "fixtures"
elif "--root" in sys.argv:
    S.ROOT = Path(sys.argv[sys.argv.index("--root") + 1]).resolve()
if not S.papers():
    print("No papers found in %s (a paper is a folder with a main.tex)." % S.ROOT)
    sys.exit(1)
failed = 0
for name in S.papers():
    d = S.paper_dir(name)
    src = S.read_tex(d)
    doc = S.Doc(src, S.Lookup(d))
    regions = list(doc.regions())
    kinds = Counter(r["kind"] for r in regions)
    locked = [it for it in doc.items if it["type"] == "locked"]
    bad = 0
    # 1. every editable region re-serialises to exactly its source
    for r in regions:
        if S.serialize(r["tokens"]) != src[r["start"]:r["end"]]:
            bad += 1
    # 2. regions do not overlap and lie in order of use
    spans = sorted((r["start"], r["end"]) for r in regions)
    for (a, b), (c, e) in zip(spans, spans[1:]):
        if c < b:
            bad += 1
    # 3. saving every region unchanged gives the same file, byte for byte
    edits = [{"start": r["start"], "end": r["end"],
              "old": src[r["start"]:r["end"]],
              "new": S.serialize(r["tokens"])} for r in regions]
    new, why = S.apply_edits(src, edits)
    if new != src:
        bad += 1
    # 4. one edit changes only that paragraph
    if regions:
        r = regions[len(regions) // 2]
        old = src[r["start"]:r["end"]]
        new, why = S.apply_edits(src, [{"start": r["start"], "end": r["end"],
                                        "old": old, "new": old + " X 50\\% \\& more"}])
        if new != src[:r["end"]] + " X 50\\% \\& more" + src[r["end"]:]:
            bad += 1
        # ... and still lands when the file has shifted underneath
        shifted = "% a line added elsewhere\n" + src
        new2, why = S.apply_edits(shifted, [{"start": r["start"], "end": r["end"],
                                             "old": old, "new": "Z"}])
        if src.count(old) == 1 and new2 != shifted.replace(old, "Z"):
            bad += 1
    covered = sum(r["end"] - r["start"] for r in regions)
    lockedraw = [it for it in locked if it["kind"] == "raw"]
    print("%-50s %4d editable %s  locked: %s  %s" % (
        name, len(regions), dict(kinds),
        dict(Counter(it["kind"] for it in locked)),
        "OK" if not bad else "FAILED (%d)" % bad))
    failed += bad
    if verbose:
        for it in lockedraw:
            print("    LOCKED  " + it["preview"][:110].replace("\n", " ⏎ "))

t = S.tokenize(r"50\% of \textbf{bold \emph{it}} at 10\,k$\Omega$ \citep{a,b}~x \textmu s")
assert S.serialize(t) == r"50\% of \textbf{bold \emph{it}} at 10\,k$\Omega$ \citep{a,b}~x \textmu s"
assert S.escape_text("a_b 5% {x} #1 $2 & c\\d ~ ^") == \
    r"a\_b 5\% \{x\} \#1 \$2 \& c\textbackslash{}d \textasciitilde{} \textasciicircum{}"
for broken in ("open {brace", "close } brace", "math $x", r"\begin{equation}"):
    try:
        S.tokenize(broken)
        print("should have locked:", broken)
        failed += 1
    except S.Lock:
        pass
print("ALL OK" if not failed else "%d FAILURES" % failed)
sys.exit(1 if failed else 0)
