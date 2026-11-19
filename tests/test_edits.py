#!/usr/bin/env python3
"""Property test: every awkward edit, on every paragraph of every paper, run
through the page's own serialiser (tests/client_ser.mjs, needs node) and
then through the server's save check.

  * An honest edit must be accepted (no false refusals).
  * What is accepted must re-read as exactly what the page showed, and must
    leave every other paragraph untouched (plan_save checks both).
  * Hand-made bad LaTeX must be refused.

Writes nothing to any paper.

    python3 tests/test_edits.py                 papers next to the editor
    python3 tests/test_edits.py --root DIR      papers in DIR
    python3 tests/test_edits.py --fixture       only the bundled example
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import server as S  # noqa: E402

args = sys.argv[1:]
if "--fixture" in args:
    S.ROOT = HERE / "fixtures"
elif "--root" in args:
    S.ROOT = Path(args[args.index("--root") + 1]).resolve()

NODE = shutil.which("node")
if not NODE:
    print("SKIPPED: node is not installed, so the page's serialiser cannot be run")
    sys.exit(0)


def ops_for(region):
    """Edits a person might make, as operations on top-level children."""
    toks, kind = region["tokens"], region["kind"]
    n = len(toks)
    out = [
        [{"op": "insert", "at": 0, "text": "[Start* "}],
        [{"op": "insert", "at": n, "text": " end 50% of a_b & c#1 $2 {x} \\ ~ ^"}],
        [{"op": "insert", "at": n, "text": "[x]"}],
        [{"op": "bold", "at": n, "text": " new bold"}],
        [{"op": "clear"}],
    ]
    for i, t in enumerate(toks):
        if t[0] == "a":
            out.append([{"op": "insert", "at": i + 1, "text": "[a]* b"}])
            out.append([{"op": "insert", "at": i + 1, "text": "s"}])
            out.append([{"op": "insert", "at": i + 1, "text": " "}])
            out.append([{"op": "delete", "at": i}])
        elif t[0] == "w":
            out.append([{"op": "empty", "at": i}])
            out.append([{"op": "into", "at": i, "text": " more"}])
            out.append([{"op": "insert", "at": i + 1, "text": "[q]"}])
        if kind in ("p", "item"):
            raw = "\n\\item " if kind == "item" else "\n\n"
            out.append([{"op": "parbreak", "at": i + 1, "raw": raw},
                        {"op": "insert", "at": i + 2, "text": "[New] part"}])
    return out


def run_node(cases):
    p = subprocess.run([NODE, str(HERE / "client_ser.mjs")], input=json.dumps(cases),
                       capture_output=True, text=True, timeout=600)
    if p.returncode:
        raise SystemExit("client_ser.mjs failed:\n" + p.stderr)
    return json.loads(p.stdout)


failed = 0
tried = 0
for name in S.papers():
    d = S.paper_dir(name)
    src = S.read_tex(d)
    lookup = S.Lookup(d)
    regions = [r for r in S.Doc(src, lookup).regions()]
    cases, meta = [], []
    for r in regions:
        for ops in ops_for(r):
            cases.append({"tokens": r["tokens"], "kind": r["kind"], "ops": ops})
            meta.append((r, ops))
    results = run_node(cases)
    bad = 0
    for (r, ops), res in zip(meta, results):
        tried += 1
        if "error" in res:
            bad += 1
            print("  JS ERROR", name, res["error"])
            continue
        edit = {"start": r["start"], "end": r["end"], "old": r["src"],
                "new": res["latex"], "shape": res["shape"]}
        new, why = S.plan_save(src, [edit], lookup)
        if ops == [{"op": "clear"}] and r["kind"] not in ("p", "item"):
            if new is not None:          # headings etc. must not be emptied
                bad += 1
                print("  EMPTIED", name, r["kind"])
            continue
        if new is None:
            bad += 1
            if bad <= 5:
                print("  REFUSED %s [%s] %s\n    ops=%s\n    new=%r" % (
                    name, r["kind"], why, json.dumps(ops)[:160], res["latex"][-160:]))
    # hand-made damage must be refused
    for r in regions[:40]:
        for evil in (r["src"] + " {", r["src"] + " }", r["src"] + " \\begin{x}",
                     r["src"] + " \\input{secret}", r["src"] + " $x",
                     r["src"] + "\n\n\\end{document}"):
            edit = {"start": r["start"], "end": r["end"], "old": r["src"], "new": evil}
            new, why = S.plan_save(src, [edit], lookup)
            if new is not None:
                bad += 1
                print("  ACCEPTED BAD", name, repr(evil[-40:]))
        # the page claims different text from what the LaTeX says
        res = run_node([{"tokens": r["tokens"], "kind": r["kind"],
                         "ops": [{"op": "insert", "at": 0, "text": "Hello "}]}])[0]
        edit = {"start": r["start"], "end": r["end"], "old": r["src"],
                "new": res["latex"].replace("Hello", "Goodbye", 1), "shape": res["shape"]}
        if S.plan_save(src, [edit], lookup)[0] is not None:
            bad += 1
            print("  ACCEPTED MISMATCH", name)
    print("%-50s %6d edits  %s" % (name, len(cases), "OK" if not bad else "FAILED (%d)" % bad))
    failed += bad

print("ALL OK (%d edits)" % tried if not failed else "%d FAILURES" % failed)
sys.exit(1 if failed else 0)
