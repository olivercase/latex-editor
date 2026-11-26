#!/usr/bin/env python3
"""Word-style editor for LaTeX papers.

    python3 server.py                  papers in the folder above this one
    python3 server.py PAPER            open that paper first
    python3 server.py --root DIR       papers in DIR (Ctrl-C to stop)

Shows a paper's PDF next to its prose. The prose is editable like a word
processor; citations, maths, references and other LaTeX appear as small
uneditable chips. "Save & recompile" writes only the paragraphs that changed
back into main.tex (every other byte of the file is left alone), rebuilds the
PDF with the same tectonic command as watch.sh and reloads it.

Standard library only. Listens on 127.0.0.1.
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import unicodedata
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

HERE = Path(__file__).resolve().parent
# Folder that holds the papers (one sub-folder with a main.tex per paper).
# Defaults to the folder above the editor; --root or LATEX_EDITOR_ROOT
# points it elsewhere.
ROOT = Path(os.environ.get("LATEX_EDITOR_ROOT") or HERE.parent).resolve()
BACKUPS = HERE / ".backups"
KEEP_BACKUPS = 60
MAX_BODY = 20 * 1024 * 1024

# --------------------------------------------------------------------------
# LaTeX -> tokens
#
# A token is one of
#   ["t", text]                          editable text
#   ["a", raw, display, cls, html?]      atom: uneditable chip that carries
#                                        its exact LaTeX source
#   ["w", pre, children, suf, style]     wrapper: \textbf{ ... } and friends
# Joining text (re-escaped), raw, and pre + children + suf gives back the
# source exactly. test_roundtrip.py checks that for every paper.
# --------------------------------------------------------------------------


class Lock(Exception):
    """Raised when a stretch of source is not safe to offer for editing."""


ESCAPED = set("%&#_${}")

# name -> (number of raw leading arguments, style)
WRAPS = {
    "textbf": (0, "b"), "textit": (0, "i"), "emph": (0, "i"),
    "underline": (0, "u"), "uline": (0, "u"),
    "textsuperscript": (0, "sup"), "textsubscript": (0, "sub"),
    "texttt": (0, "tt"), "textsc": (0, "sc"), "textrm": (0, ""),
    "textsf": (0, ""), "textnormal": (0, ""), "mbox": (0, ""),
    "revised": (0, ""), "tocheck": (0, ""), "todo": (0, "todo"),
    "unknown": (0, "unknown"),      # a fact not yet known or checked (yellow in the PDF); its text is editable
    "footnote": (0, "fn"),
    "href": (1, "link"), "hyperlink": (1, "link"), "hypertarget": (1, ""),
    "textcolor": (1, ""),
}

SYMBOLS = {
    "textmu": "µ", "textdegree": "°", "textless": "<", "textgreater": ">",
    "textasciitilde": "~", "textdaggerdbl": "‡", "textdagger": "†",
    "ldots": "…", "dots": "…", "textellipsis": "…", "textbackslash": "\\",
    "textasciicircum": "^", "textendash": "–", "textemdash": "—",
    "textquoteright": "’", "textquoteleft": "‘", "textquotedblleft": "“",
    "textquotedblright": "”", "textbullet": "•", "texttimes": "×",
    "textpm": "±", "textregistered": "®", "texttrademark": "™",
    "copyright": "©", "pounds": "£", "S": "§", "P": "¶", "ss": "ß",
    "o": "ø", "O": "Ø", "ae": "æ", "AE": "Æ", "aa": "å", "AA": "Å",
    "l": "ł", "L": "Ł", "i": "ı", "LaTeX": "LaTeX", "TeX": "TeX",
    "textbar": "|", "textquotedbl": '"', "slash": "/", "quad": "\u2003",
    "qquad": "\u2003\u2003", "textperiodcentered": "·", "textminus": "−",
    "textohm": "Ω", "textrightarrow": "→", "textleq": "≤", "textgeq": "≥",
    "textquotesingle": "'", "textasciiacute": "´", "textsection": "§",
    "textonehalf": "½", "textdiv": "÷", "textcelsius": "℃",
}
# Commands that only change spacing or layout: shown as nothing (or a space).
INVISIBLE = {"noindent", "par", "hspace", "vspace", "phantom", "hphantom",
             "vphantom", "smallskip", "medskip", "bigskip", "nobreak",
             "allowbreak", "linebreak", "pagebreak", "nopagebreak", "xspace",
             "unskip", "ignorespaces", "leavevmode", "strut", "hfill",
             "centering", "protect", "relax", "sloppy", "newpage", "clearpage",
             "setstretch", "selectfont", "fontsize", "color"}

NOARG = {
    "noindent", "par", "small", "footnotesize", "scriptsize", "tiny",
    "normalsize", "large", "Large", "LARGE", "huge", "centering",
    "raggedright", "raggedleft", "bfseries", "itshape", "clearpage",
    "newpage", "medskip", "bigskip", "smallskip", "hfill", "vfill",
    "maketitle", "tableofcontents", "linebreak", "newline", "indent",
    "sloppy", "normalfont", "rmfamily", "sffamily", "ttfamily", "upshape",
    "scshape", "mdseries", "appendix", "protect", "relax", "item",
}
INVISIBLE |= NOARG - {"item", "maketitle", "tableofcontents", "appendix"}

ACCENTS = {"'": "\u0301", "`": "\u0300", '"': "\u0308", "^": "\u0302",
           "~": "\u0303", "=": "\u0304", ".": "\u0307"}
LETTER_ACCENTS = {"c": "\u0327", "v": "\u030c", "u": "\u0306",
                  "H": "\u030b", "k": "\u0328", "r": "\u030a"}

MATH_SYMBOLS = {
    "alpha": "α", "beta": "β", "gamma": "γ", "delta": "δ", "epsilon": "ε",
    "varepsilon": "ε", "zeta": "ζ", "eta": "η", "theta": "θ", "iota": "ι",
    "kappa": "κ", "lambda": "λ", "mu": "µ", "nu": "ν", "xi": "ξ", "pi": "π",
    "rho": "ρ", "sigma": "σ", "tau": "τ", "upsilon": "υ", "phi": "φ",
    "varphi": "φ", "chi": "χ", "psi": "ψ", "omega": "ω", "Gamma": "Γ",
    "Delta": "Δ", "Theta": "Θ", "Lambda": "Λ", "Xi": "Ξ", "Pi": "Π",
    "Sigma": "Σ", "Phi": "Φ", "Psi": "Ψ", "Omega": "Ω", "times": "×",
    "cdot": "·", "pm": "±", "mp": "∓", "leq": "≤", "le": "≤", "geq": "≥",
    "ge": "≥", "approx": "≈", "sim": "∼", "simeq": "≃", "to": "→",
    "rightarrow": "→", "leftarrow": "←", "Rightarrow": "⇒", "infty": "∞",
    "propto": "∝", "neq": "≠", "ne": "≠", "circ": "°", "degree": "°",
    "ldots": "…", "dots": "…", "cdots": "⋯", "partial": "∂", "nabla": "∇",
    "sum": "Σ", "int": "∫", "in": "∈", "ll": "≪", "gg": "≫", "ell": "ℓ",
    "perp": "⊥", "parallel": "∥", "equiv": "≡", "prime": "′", "angle": "∠",
    "langle": "⟨", "rangle": "⟩", "mid": "|", "vert": "|", "lvert": "|",
    "rvert": "|", "Vert": "‖", "ast": "∗", "star": "⋆", "div": "÷",
    "hbar": "ℏ", "Re": "ℜ", "Im": "ℑ", "forall": "∀", "exists": "∃",
    "cup": "∪", "cap": "∩", "subset": "⊂", "odot": "⊙", "otimes": "⊗",
    "oplus": "⊕", "uparrow": "↑", "downarrow": "↓", "leftrightarrow": "↔",
    "lesssim": "≲", "gtrsim": "≳", "quad": "\u2003", "qquad": "\u2003",
    "left": "", "right": "", "big": "", "Big": "", "bigl": "", "bigr": "",
    "Bigl": "", "Bigr": "", "displaystyle": "", "textstyle": "",
    "limits": "", "nolimits": "", "log": "log", "ln": "ln", "exp": "exp",
    "sin": "sin", "cos": "cos", "tan": "tan", "max": "max", "min": "min",
    "arg": "arg", "det": "det", "lim": "lim", "sqrt": "√", "surd": "√",
    "mupmu": "µ", "upmu": "µ", "textmu": "µ",
}


def html_escape(s):
    return (s.replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def match_brace(s, i, end=None):
    """Index of the '}' that closes the '{' at s[i], or -1."""
    n = len(s) if end is None else end
    depth = 0
    j = i
    while j < n:
        c = s[j]
        if c == "\\":
            j += 2
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return j
        j += 1
    return -1


def match_bracket(s, i, end=None):
    """Index of the ']' that closes the '[' at s[i] on the same paragraph."""
    n = len(s) if end is None else end
    depth = 0
    j = i
    while j < n:
        c = s[j]
        if c == "\\":
            j += 2
            continue
        if c == "{":
            k = match_brace(s, j, n)
            if k < 0:
                return -1
            j = k + 1
            continue
        if c == "[":
            depth += 1
        elif c == "]":
            depth -= 1
            if depth == 0:
                return j
        elif c == "\n" and s[j + 1:j + 2] == "\n":
            return -1
        j += 1
    return -1


_MATH_GROUP = re.compile(
    r"\\(?:text|mathrm|mathit|mathbf|mathsf|mathcal|mathbb|operatorname|"
    r"textrm|textit|textbf|boldsymbol|bm|mathrmbf|vec|hat|bar|tilde|"
    r"overline|ensuremath|mbox)\s*\{([^{}]*)\}")
_MATH_FRAC = re.compile(r"\\[dt]?frac\s*\{([^{}]*)\}\s*\{([^{}]*)\}")
_MATH_CMD = re.compile(r"\\([A-Za-z]+) ?")


def math_html(raw):
    """Readable HTML for simple maths, or None when it is too involved."""
    s = raw.strip()
    for a, b in (("$$", "$$"), ("$", "$"), ("\\(", "\\)"), ("\\[", "\\]")):
        if s.startswith(a) and s.endswith(b) and len(s) >= len(a) + len(b):
            s = s[len(a):len(s) - len(b)]
            break
    for sp, rep in (("\\,", "\u2009"), ("\\;", " "), ("\\:", " "),
                    ("\\!", ""), ("\\ ", " "), ("~", "\u00a0"),
                    ("\\%", "%"), ("\\&", "&"), ("\\#", "#"), ("\\_", "_")):
        s = s.replace(sp, rep)
    for _ in range(6):
        t = _MATH_FRAC.sub(lambda m: "%s/%s" % (
            m.group(1) if len(m.group(1)) < 2 else "(" + m.group(1) + ")",
            m.group(2) if len(m.group(2)) < 2 else "(" + m.group(2) + ")"), s)
        t = _MATH_GROUP.sub(lambda m: m.group(1), t)
        if t == s:
            break
        s = t

    def sym(m):
        name = m.group(1)
        if name in MATH_SYMBOLS:
            return MATH_SYMBOLS[name]
        return m.group(0)
    s = _MATH_CMD.sub(sym, s)
    if "\\" in s:
        return None
    s = html_escape(s)
    for _ in range(4):
        t = re.sub(r"\^\{([^{}]*)\}", r"<sup>\1</sup>", s)
        t = re.sub(r"_\{([^{}]*)\}", r"<sub>\1</sub>", t)
        if t == s:
            break
        s = t
    s = re.sub(r"\^(&[a-z]+;|[^\s{}<])", r"<sup>\1</sup>", s)
    s = re.sub(r"_(&[a-z]+;|[^\s{}<])", r"<sub>\1</sub>", s)
    s = s.replace("{", "").replace("}", "")
    s = re.sub(r"\s*([=<>≈≤≥±×∼→])\s*", r" \1 ", s)
    s = s.replace("&lt;", " &lt; ").replace("&gt;", " &gt; ")
    s = re.sub(r"  +", " ", s).strip()
    return s if s else None


class Lookup:
    """Citation and cross-reference text for chips, read from .bib/.aux."""

    def __init__(self, paper_dir=None):
        self.cites = {}
        self.labels = {}
        self.macros = {}      # name -> (number of arguments, body)
        self._expanding = set()
        if paper_dir is None:
            return
        for tex in sorted(paper_dir.glob("*.tex")) + sorted(paper_dir.glob("*.sty")):
            try:
                if tex.stat().st_size < 2_000_000:
                    self._read_macros(tex.read_text(encoding="utf-8", errors="replace"))
            except OSError:
                pass
        for bib in sorted(paper_dir.glob("*.bib")):
            try:
                self._read_bib(bib.read_text(encoding="utf-8", errors="replace"))
            except OSError:
                pass
        aux = paper_dir / "main.aux"
        if aux.exists():
            try:
                text = aux.read_text(encoding="utf-8", errors="replace")
                for m in re.finditer(r"\\newlabel\{([^}]+)\}\{\{([^{}]*)\}", text):
                    self.labels[m.group(1)] = m.group(2)
            except OSError:
                pass

    _NEWCMD = re.compile(
        r"\\(?:re|provide)?newcommand\*?\s*(?:\{\s*\\([A-Za-z]+)\s*\}|\\([A-Za-z]+))"
        r"\s*(?:\[(\d)\])?\s*(?:\[[^\]\n]*\])?\s*\{")
    _DEF = re.compile(r"\\(?:g|e|x)?def\s*\\([A-Za-z]+)\s*((?:#\d)*)\s*\{")

    def _read_macros(self, text):
        text = re.sub(r"(?m)(?<!\\)%.*$", "", text)
        for m in self._NEWCMD.finditer(text):
            k = match_brace(text, m.end() - 1)
            if k > 0:
                self.macros[m.group(1) or m.group(2)] = (int(m.group(3) or 0), text[m.end():k])
        for m in self._DEF.finditer(text):
            k = match_brace(text, m.end() - 1)
            if k > 0:
                self.macros[m.group(1)] = (len(m.group(2)) // 2, text[m.end():k])

    def expand(self, name):
        """(plain text, html) of a macro that takes no arguments, or None."""
        if name not in self.macros or name in self._expanding:
            return None
        nargs, body = self.macros[name]
        if nargs or len(body) > 400:
            return None
        self._expanding.add(name)
        try:
            toks = tokenize(body.strip(), self)
        except Lock:
            return None
        finally:
            self._expanding.discard(name)
        plain = re.sub(r"\s+", " ", plain_text_shown(toks)).strip()
        if not plain:
            return None
        return plain, tokens_html(toks)

    def _read_bib(self, text):
        starts = [m for m in re.finditer(r"(?m)^\s*@(\w+)\s*\{\s*([^,\s]+)\s*,", text)]
        for n, m in enumerate(starts):
            if m.group(1).lower() in ("comment", "string", "preamble"):
                continue
            body = text[m.end(): starts[n + 1].start() if n + 1 < len(starts) else len(text)]
            self.cites[m.group(2)] = (self._authors(body), self._year(body))

    @staticmethod
    def _field(body, name):
        m = re.search(r"(?im)^\s*%s\s*=\s*" % name, body)
        if not m:
            return ""
        i = m.end()
        if i < len(body) and body[i] == "{":
            j = match_brace(body, i)
            return body[i + 1:j] if j > 0 else ""
        if i < len(body) and body[i] == '"':
            j = body.find('"', i + 1)
            return body[i + 1:j] if j > 0 else ""
        m2 = re.match(r"[^,\n]+", body[i:])
        return m2.group(0).strip() if m2 else ""

    def _authors(self, body):
        raw = self._field(body, "author") or self._field(body, "editor")
        raw = re.sub(r"\s+", " ", raw)
        if not raw:
            return ""
        people = [p.strip() for p in re.split(r"\s+and\s+", raw) if p.strip()]

        def family(p):
            if p.startswith("{") and p.endswith("}"):
                return p[1:-1]
            if "," in p:
                return p.split(",")[0].strip()
            return p.split(" ")[-1]
        names = [re.sub(r"[{}]", "", family(p)) for p in people]
        names = [re.sub(r"\\.\s*", "", n) for n in names]
        if not names:
            return ""
        if len(names) == 1:
            return names[0]
        if len(names) == 2 and names[1].lower() != "others":
            return names[0] + " and " + names[1]
        return names[0] + " et al."

    def _year(self, body):
        y = self._field(body, "year") or self._field(body, "date")
        m = re.search(r"\d{4}", y)
        return m.group(0) if m else ""

    def cite(self, name, keys):
        parts = []
        textual = name.startswith("citet") or name in ("textcite", "citeauthor")
        for k in keys:
            a, y = self.cites.get(k, ("", ""))
            if not a:
                parts.append(k)
            elif name == "citeauthor":
                parts.append(a)
            elif name in ("citeyear", "citeyearpar"):
                parts.append(y or k)
            elif textual:
                parts.append("%s (%s)" % (a, y) if y else a)
            else:
                parts.append("%s, %s" % (a, y) if y else a)
        if textual:
            return ", ".join(parts)
        return "(" + "; ".join(parts) + ")"

    def ref(self, name, key):
        num = self.labels.get(key)
        if num is None or num == "":
            return key
        return "(%s)" % num if name == "eqref" else num


CITE_RE = re.compile(r"cite[a-zA-Z]*\*?$|textcite$|parencite$|autocite$")
REF_NAMES = {"ref", "eqref", "autoref", "cref", "Cref", "pageref", "nameref"}


def tokenize(s, lookup=None):
    """Tokens for a stretch of inline LaTeX. Raises Lock if unsure."""
    lookup = lookup or Lookup()
    tokens, i = _tokens(s, 0, len(s), False, lookup, 0)
    return tokens


def _add_text(tokens, text):
    if tokens and tokens[-1][0] == "t":
        tokens[-1][1] += text
    else:
        tokens.append(["t", text])


def _tokens(s, i, n, closing, lookup, depth):
    if depth > 40:
        raise Lock("too deeply nested")
    tokens = []
    while i < n:
        c = s[i]
        if c == "\\":
            if i + 1 >= n:
                raise Lock("dangling backslash")
            nc = s[i + 1]
            if nc.isalpha() and nc.isascii():
                j = i + 1
                while j < n and s[j].isalpha() and s[j].isascii():
                    j += 1
                name = s[i + 1:j]
                i = _command(s, i, j, n, name, tokens, lookup, depth)
            elif nc in ESCAPED:
                _add_text(tokens, nc)
                i += 2
            elif nc == "\\":
                j = i + 2
                if j < n and s[j] == "*":
                    j += 1
                if j < n and s[j] == "[":
                    k = match_bracket(s, j, n)
                    if k > 0:
                        j = k + 1
                tokens.append(["a", s[i:j], "↵", "cmd"])
                i = j
            elif nc in ",;:":
                tokens.append(["a", s[i:i + 2], "\u2009" if nc == "," else " ", "sp"])
                i += 2
            elif nc in "!-/":
                tokens.append(["a", s[i:i + 2], "", "sp"])
                i += 2
            elif nc in " \n\t":
                tokens.append(["a", s[i:i + 2], " ", "sp"])
                i += 2
            elif nc == "(":
                j = s.find("\\)", i + 2, n)
                if j < 0:
                    raise Lock("unclosed \\(")
                tokens.append(_math(s[i:j + 2]))
                i = j + 2
            elif nc == "[":
                j = s.find("\\]", i + 2, n)
                if j < 0:
                    raise Lock("unclosed \\[")
                tokens.append(_math(s[i:j + 2]))
                i = j + 2
            elif nc in ACCENTS:
                j = i + 2
                if j < n and s[j] == "{":
                    k = match_brace(s, j, n)
                    if k < 0:
                        raise Lock("unclosed accent")
                    base, j = s[j + 1:k], k + 1
                elif j < n and s[j] == "\\" and s[j + 1:j + 2] in ("i", "j"):
                    base, j = s[j + 1], j + 2
                elif j < n:
                    base, j = s[j], j + 1
                else:
                    raise Lock("dangling accent")
                shown = unicodedata.normalize("NFC", base.replace("\\i", "i") + ACCENTS[nc]) \
                    if len(base.replace("\\i", "i")) == 1 else s[i:j]
                tokens.append(["a", s[i:j], shown, "sym"])
                i = j
            else:
                tokens.append(["a", s[i:i + 2], s[i:i + 2], "cmd"])
                i += 2
        elif c == "$":
            if s.startswith("$$", i):
                j = s.find("$$", i + 2, n)
                if j < 0:
                    raise Lock("unclosed $$")
                tokens.append(_math(s[i:j + 2]))
                i = j + 2
            else:
                j = i + 1
                while j < n and s[j] != "$":
                    j += 2 if s[j] == "\\" else 1
                if j >= n:
                    raise Lock("unclosed $")
                tokens.append(_math(s[i:j + 1]))
                i = j + 1
        elif c == "{":
            k = match_brace(s, i, n)
            if k < 0:
                raise Lock("unclosed {")
            if k == i + 1:
                tokens.append(["a", "{}", "", "sp"])
            else:
                kids, _ = _tokens(s, i + 1, k, True, lookup, depth + 1)
                tokens.append(["w", "{", kids, "}", ""])
            i = k + 1
        elif c == "}":
            raise Lock("unmatched }")
        elif c == "%":
            j = s.find("\n", i, n)
            j = n if j < 0 else j + 1
            while j < n and s[j] in " \t":
                j += 1
            tokens.append(["a", s[i:j], "%", "comment"])
            i = j
        elif c == "~":
            tokens.append(["a", "~", "\u00a0", "sp"])
            i += 1
        elif c == "\u00a0":
            tokens.append(["a", c, "\u00a0", "sp"])
            i += 1
        elif c in "&#^_":
            tokens.append(["a", c, c, "cmd"])
            i += 1
        else:
            j = i + 1
            while j < n and s[j] not in "\\${}%~&#^_\u00a0":
                j += 1
            _add_text(tokens, s[i:j])
            i = j
    return tokens, i


def _math(raw):
    h = math_html(raw)
    shown = raw if len(raw) <= 60 else raw[:57] + "…"
    if h is None:
        return ["a", raw, shown, "math rawmath"]
    return ["a", raw, shown, "math", h]


def _command(s, i, j, n, name, tokens, lookup, depth):
    """Handle the control word s[i:j]; append tokens; return the new index."""
    if name in ("begin", "end"):
        raise Lock("environment inside a paragraph")
    if name == "verb":
        k = j + 1 if j < n and s[j] == "*" else j
        if k >= n:
            raise Lock("bad \\verb")
        e = s.find(s[k], k + 1, n)
        if e < 0:
            raise Lock("bad \\verb")
        tokens.append(["a", s[i:e + 1], s[k + 1:e], "cmd tt"])
        return e + 1
    if name in WRAPS:
        nraw, style = WRAPS[name]
        k = j
        if k < n and s[k] == "[":
            e = match_bracket(s, k, n)
            if e > 0:
                k = e + 1
        ok = True
        for _ in range(nraw):
            if k < n and s[k] == "{":
                e = match_brace(s, k, n)
                if e < 0:
                    raise Lock("unclosed {")
                k = e + 1
            else:
                ok = False
        if ok and k < n and s[k] == "{":
            e = match_brace(s, k, n)
            if e < 0:
                raise Lock("unclosed {")
            if e == k + 1:
                tokens.append(["a", s[i:e + 1], "", "sp"])
            else:
                kids, _ = _tokens(s, k + 1, e, True, lookup, depth + 1)
                tokens.append(["w", s[i:k + 1], kids, "}", style])
            return e + 1
    # Generic command: take the argument groups that follow it directly.
    k = j
    args = []
    if name in NOARG or name in SYMBOLS:
        if s[j:j + 2] == "{}":
            k = j + 2
    else:
        if k < n and s[k] == "*":
            k += 1
        while k < n:
            # Options come before the braces (\cite[p. 5]{key}); a "[" after
            # a brace group is ordinary text, e.g. "\cite{key} [sic]" typed
            # without the space.
            if s[k] == "[" and not args:
                e = match_bracket(s, k, n)
                if e < 0:
                    break
                k = e + 1
            elif s[k] == "{":
                e = match_brace(s, k, n)
                if e < 0:
                    raise Lock("unclosed {")
                args.append(s[k + 1:e])
                k = e + 1
            else:
                break
    if k == j:
        # No arguments: TeX swallows the spaces after a control word.
        while k < n and s[k] in " \t":
            k += 1
    raw = s[i:k]
    if name in SYMBOLS:
        tokens.append(["a", raw, SYMBOLS[name], "sym"])
    elif name in INVISIBLE:
        sp = " " if name in ("hspace", "hfill", "quad") or raw.endswith(" ") else ""
        tokens.append(["a", raw, sp, "sp"])
    elif not any(x.strip() for x in args) and "[" not in raw and lookup.expand(name):
        plain, html = lookup.expand(name)
        tokens.append(["a", raw, plain, "macro", html])
    elif CITE_RE.match(name) and args:
        keys = [x.strip() for x in args[-1].split(",") if x.strip()]
        tokens.append(["a", raw, lookup.cite(name, keys), "cite"])
    elif name in REF_NAMES and args:
        tokens.append(["a", raw, lookup.ref(name, args[-1].strip()), "ref"])
    elif name == "label":
        tokens.append(["a", raw, "", "sp"])
    elif name == "ensuremath" and args:
        tokens.append(_math_from(raw, args[-1]))
    elif name in ("url", "nolinkurl", "doilink") and args:
        tokens.append(["a", raw, args[-1], "cmd tt"])
    elif name == "reviewnote":
        who = args[0] if args else ""
        initials = "".join(w[0] for w in re.findall(r"[^\W\d_]+", who)[:3]).upper() or "•"
        tokens.append(["a", raw, initials, "note"])
    elif args and len(args) <= 3 and re.search(r"[^\W\d_]", args[-1]) \
            and "\\begin" not in raw and len(raw) < 600:
        # \tbc{...}, \refadd{...}, \SI{5}{ms}: show what the arguments say
        try:
            parts = [plain_text_shown(tokenize(a, lookup)) for a in args]
            shown = re.sub(r"\s+", " ", " ".join(p for p in parts if p.strip())).strip()
        except Lock:
            shown = ""
        tokens.append(["a", raw, shown or raw.strip()[:30], "cmdtext" if shown else "cmd"])
    else:
        shown = raw.strip()
        if len(shown) > 30:
            shown = shown[:27] + "…"
        tokens.append(["a", raw, shown, "cmd"])
    return k


def _math_from(raw, inner):
    h = math_html(inner)
    shown = raw if len(raw) <= 60 else raw[:57] + "…"
    if h is None:
        return ["a", raw, shown, "math rawmath"]
    return ["a", raw, shown, "math", h]


def escape_text(t):
    out = []
    for ch in t:
        if ch in ESCAPED:
            out.append("\\" + ch)
        elif ch == "\\":
            out.append("\\textbackslash{}")
        elif ch == "~":
            out.append("\\textasciitilde{}")
        elif ch == "^":
            out.append("\\textasciicircum{}")
        else:
            out.append(ch)
    return "".join(out)


def serialize(tokens):
    """Tokens back to LaTeX. The browser does the same from the DOM."""
    out = []
    for t in tokens:
        if t[0] == "t":
            out.append(escape_text(t[1]))
        elif t[0] == "a":
            out.append(t[1])
        else:
            out.append(t[1] + serialize(t[2]) + t[3])
    return "".join(out)


def plain_text_shown(tokens):
    """The text a reader sees, chips included."""
    out = []
    for t in tokens:
        if t[0] == "t":
            out.append(t[1])
        elif t[0] == "w":
            out.append(plain_text_shown(t[2]))
        elif "comment" not in t[3]:
            out.append(re.sub(r"<[^>]+>", "", t[4]) if len(t) > 4 and t[4] else t[2])
    return "".join(out)


_WRAP_TAG = {"b": "b", "i": "i", "u": "u", "sup": "sup", "sub": "sub",
             "tt": "code", "sc": "span"}


def tokens_html(tokens):
    """Read-only HTML for a stretch of tokens (table cells, macro values)."""
    out = []
    for t in tokens:
        if t[0] == "t":
            out.append(html_escape(t[1]))
        elif t[0] == "a":
            if "comment" in t[3]:
                continue
            if "math" in t[3]:
                out.append('<span class="tex" data-tex="%s">%s</span>' % (
                    html_escape(t[1]), t[4] if len(t) > 4 and t[4] else html_escape(t[2])))
            else:
                out.append(t[4] if len(t) > 4 and t[4] else html_escape(t[2]))
        else:
            tag = _WRAP_TAG.get(t[4])
            inner = tokens_html(t[2])
            out.append("<%s>%s</%s>" % (tag, inner, tag) if tag else inner)
    return "".join(out)


RULES = re.compile(r"\\(?:hline|toprule|midrule|bottomrule|cmidrule(?:\([^)]*\))?(?:\{[^}]*\})?|"
                   r"cline\{[^}]*\}|addlinespace(?:\[[^\]]*\])?|endhead|endfirsthead|endfoot|endlastfoot|"
                   r"rowcolor(?:\[[^\]]*\])?\{[^}]*\}|noalign\{[^}]*\})")


def table_rows(chunk, lookup):
    """The cells of the first tabular in chunk as HTML, or None."""
    m = re.search(r"\\begin\{(tabular\*?|tabularx|longtable|tabulary)\}", chunk)
    if not m:
        return None
    k = m.end()
    if m.group(1) in ("tabular*", "tabularx", "tabulary") and chunk[k:k + 1] == "{":
        k = match_brace(chunk, k) + 1
    if chunk[k:k + 1] == "[":
        k = match_bracket(chunk, k) + 1
    if chunk[k:k + 1] != "{":
        return None
    e = match_brace(chunk, k)
    end = chunk.find("\\end{%s}" % m.group(1), e)
    if e < 0 or end < 0:
        return None
    body = re.sub(r"(?m)(?<!\\)%.*$", "", chunk[e + 1:end])
    rows, cells, cur, i, depth = [], [], [], 0, 0
    while i < len(body):
        c = body[i]
        if c == "\\" and body[i + 1:i + 2] == "\\" and depth == 0:
            cells.append("".join(cur)); rows.append(cells); cells, cur = [], []
            i += 2
            if body[i:i + 1] == "[":
                j = match_bracket(body, i)
                i = j + 1 if j > 0 else i
            continue
        if c == "\\":
            cur.append(body[i:i + 2]); i += 2; continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
        elif c == "&" and depth == 0:
            cells.append("".join(cur)); cur = []; i += 1; continue
        cur.append(c); i += 1
    if "".join(cur).strip():
        cells.append("".join(cur))
    if cells:
        rows.append(cells)
    out = []
    for row in rows:
        html_row = []
        for cell in row:
            cell = RULES.sub("", cell).strip()
            span = 1
            mc = re.match(r"\\multicolumn\{(\d+)\}\{[^}]*\}\{", cell)
            if mc:
                span = int(mc.group(1))
                j = match_brace(cell, mc.end() - 1)
                cell = cell[mc.end():j] if j > 0 else cell
            mr = re.match(r"\\multirow\{[^}]*\}\{[^}]*\}\{", cell)
            if mr:
                j = match_brace(cell, mr.end() - 1)
                cell = cell[mr.end():j] if j > 0 else cell
            try:
                html = tokens_html(tokenize(cell, lookup))
            except Lock:
                html = html_escape(cell)
            html_row.append([html, span])
        if any(h.strip() for h, _ in html_row):
            out.append(html_row)
    return out or None


def plain_text(tokens):
    out = []
    for t in tokens:
        if t[0] == "t":
            out.append(t[1])
        elif t[0] == "w":
            out.append(plain_text(t[2]))
        else:
            out.append(" ")
    return "".join(out)


# --------------------------------------------------------------------------
# main.tex -> ordered list of things to show
# --------------------------------------------------------------------------

TRANSPARENT = {"abstract", "itemize", "enumerate", "quote", "quotation",
               "description", "proposition", "theorem", "lemma", "document"}
MATH_ENVS = {"equation", "align", "gather", "multline", "eqnarray",
             "displaymath", "math", "flalign", "alignat"}
MARK_RE = re.compile(r"\\(?:todo|unknown)[ \t]*\{")
HEAD_RE = re.compile(
    r"[ \t]*\\(section|subsection|subsubsection|paragraph|subparagraph|frontsection)\*?"
    r"[ \t]*(?:\[[^\]\n]*\])?[ \t]*\{")
HEAD_KIND = {"section": "h1", "frontsection": "h1",      # \frontsection: unnumbered section of a thesis
             "subsection": "h2", "subsubsection": "h3",
             "paragraph": "h4", "subparagraph": "h4"}
BEGIN_RE = re.compile(r"[ \t]*\\begin\{([A-Za-z]+\*?)\}")
END_RE = re.compile(r"[ \t]*\\end\{([A-Za-z]+\*?)\}[ \t]*")
ITEM_RE = re.compile(r"[ \t]*\\item\b[ \t]*")
CAPTION_RE = re.compile(r"\\caption\*?[ \t]*(?:\[[^\]\n]*\])?[ \t]*\{|\\captionof\{[a-z]+\}\{")
TRAIL_RE = re.compile(r"(?:[ \t]*(?:\\label\{[^{}]*\}|\\hypertarget\{[^{}]*\}\{\}))*[ \t]*")
GRAPHICS_RE = re.compile(r"\\includegraphics\*?(?:\[[^\]]*\])?\{([^{}]+)\}")
LABEL_RE = re.compile(r"\\label\{([^{}]+)\}")


class Doc:
    def __init__(self, src, lookup=None):
        self.src = src
        self.lookup = lookup or Lookup()
        self.items = []
        self.nregions = 0
        self._parse()

    # -- helpers ---------------------------------------------------------
    def _region(self, kind, start, end):
        """An editable region dict for src[start:end], or None."""
        src = self.src
        while start < end and src[start] in " \t\r\n":
            start += 1
        while end > start and src[end - 1] in " \t\r\n":
            end -= 1
        if end <= start or "\r" in src[start:end]:
            return None
        try:
            tokens = tokenize(src[start:end], self.lookup)
        except Lock:
            return None
        if serialize(tokens) != src[start:end]:
            return None
        if kind in ("p", "item") and not re.search(r"[^\W\d_]{2}", plain_text(tokens)):
            return None
        self.nregions += 1
        return {"type": "region", "kind": kind, "id": "r%d" % self.nregions,
                "start": start, "end": end, "src": src[start:end],
                "tokens": tokens}

    def _locked(self, start, end, kind="raw", **extra):
        if end <= start or not self.src[start:end].strip():
            return
        item = {"type": "locked", "kind": kind, "start": start, "end": end,
                "preview": self.src[start:end].strip()[:1500]}
        item.update(extra)
        marks = self._marks(start, end, item.get("regions") or [])
        if marks:
            item["regions"] = list(item.get("regions") or []) + marks
        self.items.append(item)

    def _marks(self, start, end, taken):
        """Placeholders (\\todo{...}, \\unknown{...}) inside a locked block, as editable regions of kind
        "mark", so that no marked text is out of reach. Each region is the whole command, so its text
        can be typed and the mark itself can be cleared. Marks already inside a caption are left to it."""
        src, out = self.src, []
        spans = [(r["start"], r["end"]) for r in taken]
        for m in MARK_RE.finditer(src, start, end):
            a = m.start()
            if any(x <= a < y for x, y in spans):
                continue
            line = src[src.rfind("\n", 0, a) + 1:a]
            if re.search(r"(?<!\\)%", line):
                continue                                    # commented out
            k = match_brace(src, m.end() - 1, end)
            if k < 0:
                continue
            r = self._region("mark", a, k + 1)
            if not r:
                continue
            # What stands before the mark on its line (or since the last \\\\), as a label for the page.
            before = re.split(r"\\\\", line)[-1]
            before = re.sub(r"\\(?:color|textcolor|fontsize)\*?(?:\{[^{}]*\})+", " ", before)
            before = re.sub(r"\{[\d.\s]*(?:mm|pt|cm|em|in)?\}", " ", before)       # lengths and counts
            before = re.sub(r"\\[A-Za-z]+\*?(\[[^\]]*\])?|[{}&]", " ", before)
            r["label"] = re.sub(r"\s+", " ", before).strip()[-70:]
            spans.append((a, k + 1))
            out.append(r)
        return out

    def _flush(self, kind, start, end):
        if start is None:
            return
        r = self._region(kind, start, end)
        if r:
            self.items.append(r)
        elif not self._macro_block(start, end):
            self._locked(start, end)

    def _macro_block(self, start, end):
        """A paragraph that is one command, e.g. \\bigfigure{file}{label}{legend}:
        offer its last argument for editing if that argument is prose."""
        src = self.src
        while start < end and src[start] in " \t\r\n":
            start += 1
        while end > start and src[end - 1] in " \t\r\n":
            end -= 1
        m = re.compile(r"\\([A-Za-z]+)\*?").match(src, start, end)
        if not m:
            return False
        k, args = m.end(), []
        while k < end and src[k] == "{":
            e = match_brace(src, k, end)
            if e < 0:
                return False
            args.append((k + 1, e))
            k = e + 1
        if k != end or not args:
            return False
        a, b = args[-1]
        if len(re.findall(r"[^\W\d_]{2,}", src[a:b])) < 4:
            return False
        r = self._region("caption", a, b)
        if not r:
            return False
        name = m.group(1)
        extra = {"regions": [r], "env": name}
        kind = "note"
        if "fig" in name.lower() and len(args) >= 2:
            kind = "figure"
            extra["graphics"] = [src[args[0][0]:args[0][1]]]
            if len(args) >= 3:
                extra["number"] = self.lookup.labels.get(src[args[1][0]:args[1][1]], "")
        self._locked(start, end, kind, **extra)
        return True

    def _line_end(self, pos):
        j = self.src.find("\n", pos)
        return len(self.src) if j < 0 else j + 1

    # -- parse -----------------------------------------------------------
    def _parse(self):
        src = self.src
        m = re.search(r"(?m)^[ \t]*\\begin\{document\}[ \t]*\n?", src)
        if not m:
            return
        body_start = m.end()
        e = src.rfind("\\end{document}")
        body_end = e if e > body_start else len(src)

        for m2 in re.finditer(r"(?m)^[ \t]*\\title[ \t]*(?:\[[^\]\n]*\])?[ \t]*\{", src[:m.start()]):
            b = m2.end() - 1
            k = match_brace(src, b)
            if k > 0:
                r = self._region("title", b + 1, k)
                if r:
                    self.items.append(r)
            break

        pos = body_start
        par_start = None   # start of the paragraph being collected
        par_end = None
        par_kind = "p"
        par_outer = None   # where "\item" starts, for a bullet

        def flush():
            nonlocal par_start, par_end, par_kind, par_outer
            n0 = len(self.items)
            self._flush(par_kind, par_start, par_end)
            if par_kind == "item" and par_outer is not None and len(self.items) > n0 \
                    and self.items[-1]["type"] == "region":
                self.items[-1]["outer"] = par_outer
            par_start, par_end, par_kind, par_outer = None, None, "p", None

        while pos < body_end:
            le = min(self._line_end(pos), body_end)
            line = src[pos:le]
            stripped = line.strip()
            if not stripped:
                flush()
                pos = le
                continue
            if stripped.startswith("%"):
                flush()
                pos = le
                continue
            mb = BEGIN_RE.match(line)
            if mb:
                name = mb.group(1)
                flush()
                if name.rstrip("*") in TRANSPARENT:
                    self.items.append({"type": "env", "name": name.rstrip("*"),
                                       "open": True})
                    k = pos + mb.end()
                    while k < le and src[k] == "[":
                        e2 = match_bracket(src, k, le)
                        if e2 < 0:
                            break
                        k = e2 + 1
                    if src[k:le].strip():
                        par_start, par_end = k, le
                    pos = le
                    continue
                pos = self._locked_env(pos, name, body_end)
                continue
            me = END_RE.fullmatch(line.rstrip("\n"))
            if me and me.group(1).rstrip("*") in TRANSPARENT:
                flush()
                self.items.append({"type": "env", "name": me.group(1).rstrip("*"),
                                   "open": False})
                pos = le
                continue
            if stripped.startswith("\\[") or stripped.startswith("$$"):
                close = "\\]" if stripped.startswith("\\[") else "$$"
                a = src.find(stripped[:2], pos)
                j = src.find(close, a + 2, body_end)
                if j > 0:
                    flush()
                    stop = self._line_end(j)
                    self._locked(pos, stop, "equation")
                    pos = stop
                    continue
            mh = HEAD_RE.match(line)
            if mh:
                b = pos + mh.end() - 1
                k = match_brace(src, b, body_end)
                if k > 0:
                    flush()
                    r = self._region(HEAD_KIND[mh.group(1)], b + 1, k)
                    if r:
                        self.items.append(r)
                    else:
                        self._locked(pos, k + 1)
                    le = min(self._line_end(k), body_end)
                    mt = TRAIL_RE.match(src, k + 1, le)
                    rest = mt.end() if mt else k + 1
                    if src[rest:le].strip():
                        par_start, par_end = rest, le
                    pos = le
                    continue
            mi = ITEM_RE.match(line)
            if mi:
                flush()
                k = pos + mi.end()
                if k < le and src[k] == "[":
                    e2 = match_bracket(src, k, le)
                    if e2 > 0:
                        k = e2 + 1
                par_kind = "item"
                par_outer = pos + line.index("\\item")
                par_start, par_end = k, le
                pos = le
                continue
            if par_start is None:
                par_start = pos
            par_end = le
            pos = le
        flush()

    def _locked_env(self, pos, name, body_end):
        """Lock \\begin{name} ... \\end{name}; expose its captions."""
        src = self.src
        opener, closer = "\\begin{%s}" % name, "\\end{%s}" % name
        depth = 0
        stop = None
        for m in re.finditer(re.escape(opener) + "|" + re.escape(closer), src[pos:body_end]):
            depth += 1 if m.group(0) == opener else -1
            if depth <= 0:
                stop = pos + m.end()
                break
        if stop is None:
            stop = body_end
        le = min(self._line_end(stop), body_end)
        if not src[stop:le].strip():
            stop = le
        chunk = src[pos:stop]
        base = name.rstrip("*")
        if base in MATH_ENVS:
            kind = "equation"
        elif base.startswith("figure") or (GRAPHICS_RE.search(chunk) and "tabular" not in chunk):
            kind = "figure"
        elif base in ("table", "longtable", "tabular", "tabularx", "landscape",
                      "sidewaystable") or "\\begin{tabular" in chunk or "\\begin{longtable" in chunk:
            kind = "table"
        else:
            kind = "raw"
        regions = []
        for mc in CAPTION_RE.finditer(chunk):
            b = pos + mc.end() - 1
            k = match_brace(src, b, stop)
            if k > 0:
                r = self._region("caption", b + 1, k)
                if r:
                    regions.append(r)
        extra = {"regions": regions, "env": name}
        ml = LABEL_RE.search(chunk)
        if ml:
            extra["number"] = self.lookup.labels.get(ml.group(1), "")
        if kind == "figure":
            extra["graphics"] = GRAPHICS_RE.findall(chunk)
        if kind == "table":
            try:
                rows = table_rows(chunk, self.lookup)
            except Exception:       # a preview only; never block loading
                rows = None
            if rows:
                extra["rows"] = rows
        if kind == "equation":
            extra["tex"] = chunk.strip()
            inner = chunk.strip()[len(opener):]
            inner = inner[:inner.rfind(closer)] if closer in inner else inner
            inner = LABEL_RE.sub("", inner)
            extra["html"] = math_html(inner.replace("&", "").replace("\\\\", " ; ")
                                      .replace("\\nonumber", "").replace("\\notag", ""))
        self._locked(pos, stop, kind, **extra)
        return stop

    def regions(self):
        for it in self.items:
            if it["type"] == "region":
                yield it
            elif it["type"] == "locked":
                for r in it.get("regions", ()):
                    yield r


# --------------------------------------------------------------------------
# Files
# --------------------------------------------------------------------------

SKIP_DIRS = ("house", "editor", "archive")


def _is_paper(d):
    return (d / "main.tex").is_file() and d.resolve() != HERE


def papers():
    """Paper names, as paths relative to ROOT. A paper is a folder with a
    main.tex, either directly in ROOT or one level down inside a plain folder
    that groups papers (published/, submitted/ ...). Folders inside a paper
    are never searched."""
    out = []
    try:
        tops = sorted(p for p in ROOT.iterdir() if p.is_dir())
    except OSError:
        return out
    for d in tops:
        if d.name in SKIP_DIRS or d.name.startswith(".") or d.resolve() == HERE:
            continue
        if _is_paper(d):
            out.append(d.name)
            continue
        try:
            subs = sorted(p for p in d.iterdir() if p.is_dir())
        except OSError:
            continue
        for sub in subs:
            if sub.name in SKIP_DIRS or sub.name.startswith("."):
                continue
            if _is_paper(sub):
                out.append(d.name + "/" + sub.name)
    return out


def paper_dir(name):
    if name not in papers():
        raise KeyError(name)
    return ROOT / name


def paper_key(d):
    """The paper's name as papers() gives it."""
    return d.relative_to(ROOT).as_posix()


def read_tex(d):
    with open(d / "main.tex", "r", encoding="utf-8", newline="") as f:
        return f.read()


def digest(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def mtime(p):
    try:
        return p.stat().st_mtime
    except OSError:
        return 0


def write_tex(d, text):
    """Atomic write that keeps the file's permissions."""
    target = d / "main.tex"
    tmp = d / ".main.tex.editor-tmp"
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    try:
        shutil.copymode(target, tmp)
    except OSError:
        pass
    os.replace(tmp, target)


def backup(d, text):
    folder = BACKUPS / paper_key(d)
    if ROOT != HERE.parent.resolve():
        # papers from another --root get their own backup folders
        folder = BACKUPS / ("root-" + hashlib.sha1(str(ROOT).encode()).hexdigest()[:10]) / paper_key(d)
    folder.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y-%m-%d_%H%M%S")
    path = folder / ("main.%s.tex" % stamp)
    n = 1
    while path.exists():
        n += 1
        path = folder / ("main.%s_%d.tex" % (stamp, n))
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(text)
    old = sorted(folder.glob("main.*.tex"))
    for p in old[:-KEEP_BACKUPS]:
        try:
            p.unlink()
        except OSError:
            pass
    return path


def apply_edits(text, edits):
    """Splice edits into text. Each edit must find its old source intact.

    Returns (new_text, None) or (None, reason)."""
    placed = []
    for e in edits:
        try:
            start, end, old, new = int(e["start"]), int(e["end"]), e["old"], e["new"]
        except (KeyError, TypeError, ValueError):
            return None, "malformed edit"
        if not isinstance(old, str) or not isinstance(new, str):
            return None, "malformed edit"
        if "\r" in new or "\x00" in new:
            return None, "an edit contains characters that cannot be saved"
        if not (0 <= start <= end <= len(text)) or text[start:end] != old:
            # The file changed underneath us; find the paragraph again.
            if not old or text.count(old) != 1:
                return None, ("main.tex was changed by another program and a "
                              "paragraph you edited could not be found again")
            start = text.index(old)
            end = start + len(old)
        placed.append((start, end, new))
    placed.sort()
    for (a, b, _), (c, d, _) in zip(placed, placed[1:]):
        if c < b:
            return None, "edits overlap"
    for start, end, new in reversed(placed):
        text = text[:start] + new + text[end:]
    return text, None


# --------------------------------------------------------------------------
# Save-time checks. The browser turns what you typed into LaTeX; before any
# of it reaches main.tex the server reads that LaTeX back and makes sure it
# means what the page showed, touches only real paragraphs, and leaves every
# other paragraph as it was. If anything disagrees, nothing is written.
# --------------------------------------------------------------------------

# Wrappers the browser itself may create (Cmd-B, Cmd-I, ...).
NEW_WRAPS = {"\\textbf{", "\\textit{", "\\underline{", "\\textsuperscript{",
             "\\textsubscript{"}
# Chips the browser itself may create (paragraph or bullet breaks, guards).
NEW_ATOMS = {"\\item", ""}
# What escape_text writes for these characters; read back they are chips,
# so both sides are compared as the plain character.
ESCAPE_ATOMS = {"\\textbackslash": "\\", "\\textasciitilde": "~",
                "\\textasciicircum": "^"}
BLANK_LINE = re.compile(r"\n[ \t]*\n")


def _norm_raw(raw):
    r = raw
    while True:
        s = r.rstrip(" \t\n")
        if s.endswith("{}") and s != "{}" and not s.endswith("\\{}"):
            s = s[:-2]
        elif s == "{}":
            s = ""
        if s == r:
            return s
        r = s


def shape(tokens):
    """What a paragraph means, ignoring how the LaTeX spells it: its words
    (spacing ignored) and its chips and wrappers, in order."""
    out = []

    def text(t):
        t = re.sub(r"\s+", "", t)
        if not t:
            return
        if out and out[-1][0] == "t":
            out[-1][1] += t
        else:
            out.append(["t", t])

    def walk(toks):
        for t in toks:
            if t[0] == "t":
                text(t[1])
            elif t[0] == "a":
                r = _norm_raw(t[1])
                if r in ESCAPE_ATOMS:
                    text(ESCAPE_ATOMS[r])
                elif r:
                    out.append(["a", r])
            elif t[0] == "w":
                out.append(["(", t[1]])
                walk(t[2])
                out.append([")", t[3]])
            else:
                raise ValueError("bad token")
    walk(tokens)
    return out


def _flat_shape(client_shape):
    """The browser's description of a paragraph, as tokens for shape()."""
    if not isinstance(client_shape, list) or len(client_shape) > 200000:
        raise ValueError("bad shape")
    toks, stack = [], []
    cur = toks
    for x in client_shape:
        if not (isinstance(x, list) and len(x) == 2 and isinstance(x[1], str)):
            raise ValueError("bad shape")
        k, v = x
        if k == "t":
            cur.append(["t", v])
        elif k == "a":
            cur.append(["a", v])
        elif k == "(":
            w = ["w", v, [], None, ""]
            cur.append(w)
            stack.append(cur)
            cur = w[2]
        elif k == ")":
            if not stack:
                raise ValueError("bad shape")
            cur = stack.pop()
            cur[-1][3] = v
        else:
            raise ValueError("bad shape")
    if stack:
        raise ValueError("bad shape")
    return shape(toks)


def _atoms_and_wraps(tokens, atoms, wraps, depth=0):
    """Collect chips and wrapper openings; return False if a blank line
    (paragraph break) sits anywhere but the top level."""
    ok = True
    for t in tokens:
        if t[0] == "t":
            if depth and BLANK_LINE.search(t[1]):
                ok = False
        elif t[0] == "a":
            atoms.append(_norm_raw(t[1]))
            if depth and BLANK_LINE.search(t[1]):
                ok = False
        else:
            wraps.add(t[1])
            ok = _atoms_and_wraps(t[2], atoms, wraps, depth + 1) and ok
    return ok


NOTE_RE = re.compile(r"\\reviewnote\{([^{}\\%]{1,80})\}\{([^{}\\%]{0,40})\}\{(.*)\}", re.S)


def safe_note(raw, lookup):
    """True if raw is a comment the editor itself could have made:
    \\reviewnote{name}{tag}{plain text}, with only escaped characters."""
    if "reviewnote" not in lookup.macros or len(raw) > 4000:
        return False
    m = NOTE_RE.fullmatch(raw.strip())
    if not m or "\n\n" in raw:
        return False
    try:
        toks = tokenize(m.group(3), lookup)
    except Lock:
        return False
    allowed = {"\\textbackslash", "\\textasciitilde", "\\textasciicircum", "~"}
    return all(t[0] == "t" or (t[0] == "a" and _norm_raw(t[1]) in allowed) for t in toks)


def check_edit(region, new, client_shape, lookup):
    """None if `new` may replace `region`, else the reason it may not."""
    if not isinstance(new, str) or "\r" in new or "\x00" in new:
        return "it contains characters that cannot be saved"
    if new == region["src"]:
        return None
    if not new.strip(" \t\n"):
        if new != "":
            return "it contains only spaces"
        if region["kind"] in ("p", "item", "mark"):
            return None                     # the paragraph, bullet or placeholder is deleted
        return "a heading, title or caption cannot be left empty"
    if new != new.strip(" \t\n"):
        return "it starts or ends with a line break"
    try:
        toks = tokenize(new, lookup)
    except Lock as exc:
        return "the LaTeX it would write is not balanced (%s)" % exc
    if serialize(toks) != new:
        return "the LaTeX it would write does not read back the same"
    atoms, wraps = [], set()
    top_only = _atoms_and_wraps(toks, atoms, wraps)
    kind = region["kind"]
    if BLANK_LINE.search(new):
        if kind not in ("p", "item"):
            return "a heading or caption cannot be split into paragraphs"
        if not top_only:
            return "a paragraph break would fall inside bold, italic or another wrapper"
    if kind == "mark":
        # Inside a locked block (a table cell, a drawing) a square bracket could end an optional argument.
        for ch in "[]":
            if new.count(ch) > region["src"].count(ch):
                return "a placeholder inside a table or drawing cannot contain [ or ]"
    if kind == "item" and new.startswith(("[", "*")):
        return "a bullet would start with [ or * and change the list"
    old_atoms, old_wraps = [], set()
    _atoms_and_wraps(region["tokens"], old_atoms, old_wraps)
    pool = {}
    for a in old_atoms:
        pool[a] = pool.get(a, 0) + 1
    for a in atoms:
        if pool.get(a, 0) > 0:
            pool[a] -= 1
        elif a == "\\item" and kind != "item":
            return "a new bullet can only be made inside a list"
        elif a.startswith("\\reviewnote") and kind in ("p", "item") and safe_note(a, lookup):
            continue
        elif a not in NEW_ATOMS and a not in ESCAPE_ATOMS:
            return "it contains LaTeX that was not in the paragraph before: %s" % a[:60]
    for w in wraps:
        if w not in old_wraps and w not in NEW_WRAPS and w != "{":
            return "it contains a command that was not in the paragraph before: %s" % w[:60]
    if client_shape is not None:
        try:
            want = _flat_shape(client_shape)
        except ValueError:
            return "the page sent a malformed description of the paragraph"
        if shape(toks) != want:
            return "the LaTeX it would write would not show what you typed"
    return None


def _region_key(r):
    return (r["kind"], r["src"])


def plan_save(text, edits, lookup):
    """Check and splice. Returns (new_text, None) or (None, reason)."""
    if not isinstance(edits, list):
        return None, "malformed request"
    doc = Doc(text, lookup)
    regions = list(doc.regions())
    by_span = {(r["start"], r["end"]): r for r in regions}
    by_src = {}
    for r in regions:
        by_src.setdefault(r["src"], []).append(r)
    used = set()
    fixed = []
    for n, e in enumerate(edits, 1):
        if not isinstance(e, dict):
            return None, "malformed edit"
        old = e.get("old")
        try:
            span = (int(e.get("start")), int(e.get("end")))
        except (TypeError, ValueError):
            return None, "malformed edit"
        r = by_span.get(span)
        if r is None or r["src"] != old:
            # The file changed underneath us; find the paragraph again.
            cands = by_src.get(old, [])
            if len(cands) != 1:
                return None, ("main.tex was changed by another program and a "
                              "paragraph you edited could not be found again")
            r = cands[0]
        if r["id"] in used:
            return None, "the same paragraph was sent twice"
        used.add(r["id"])
        if isinstance(e.get("new"), str):
            # Spaces and newlines at a paragraph's edges mean nothing in
            # LaTeX (the region itself excludes them), so drop them.
            e = dict(e, new=e["new"].strip(" \t\n"))
        why = check_edit(r, e.get("new"), e.get("shape"), lookup)
        if why:
            snippet = re.sub(r"\s+", " ", plain_text(r["tokens"])).strip()[:70]
            return None, "Paragraph “%s…” was not saved: %s." % (snippet, why)
        start = r["start"]
        if e["new"] == "" and r["kind"] == "item" and "outer" in r:
            # Deleting all of a bullet's text deletes the bullet, with its line.
            start = r["outer"]
            j = start
            while j > 0 and text[j - 1] in " \t":
                j -= 1
            if j > 0 and text[j - 1] == "\n":
                start = j - 1
        fixed.append({"start": start, "end": r["end"], "old": text[start:r["end"]],
                      "new": e["new"]})
    new_text, why = apply_edits(text, fixed)
    if new_text is None:
        return None, why
    # Every paragraph that was not edited must come back exactly as it was.
    after = {}
    for r in Doc(new_text, lookup).regions():
        k = _region_key(r)
        after[k] = after.get(k, 0) + 1
    for r in regions:
        if r["id"] in used:
            continue
        k = _region_key(r)
        if after.get(k, 0) <= 0:
            snippet = re.sub(r"\s+", " ", plain_text(r["tokens"])).strip()[:70]
            return None, ("saving would have changed how another paragraph is "
                          "read (“%s…”), so nothing was written" % snippet)
        after[k] -= 1
    for marker in ("\\begin{document}", "\\end{document}"):
        if new_text.count(marker) != text.count(marker):
            return None, "saving would have damaged the structure of main.tex"
    return new_text, None


# --------------------------------------------------------------------------
# Build (same commands as house/watch.sh)
# --------------------------------------------------------------------------

BUILD_LOCK = threading.Lock()
FILE_LOCK = threading.Lock()     # one read-check-write of main.tex at a time
FAST = ["-X", "compile", "--only-cached", "--keep-intermediates",
        "--reruns", "0", "--synctex", "--keep-logs", "main.tex"]
FULL = ["-X", "compile", "--keep-intermediates", "--synctex", "--keep-logs",
        "main.tex"]


def tectonic():
    return shutil.which("tectonic") or "/opt/homebrew/bin/tectonic"


def other_builders(d):
    """PIDs of tectonic processes someone else started in this paper."""
    try:
        out = subprocess.run(["pgrep", "-x", "tectonic"], capture_output=True,
                             text=True, timeout=5).stdout.split()
    except (OSError, subprocess.SubprocessError):
        return []
    found = []
    for pid in out:
        try:
            cwd = subprocess.run(["lsof", "-a", "-p", pid, "-d", "cwd", "-Fn"],
                                 capture_output=True, text=True, timeout=5).stdout
        except (OSError, subprocess.SubprocessError):
            continue
        for ln in cwd.splitlines():
            if ln.startswith("n") and os.path.realpath(ln[1:]) == os.path.realpath(str(d)):
                found.append(pid)
    return found


def log_errors(d):
    out = []
    try:
        for ln in (d / "build.log").read_text(encoding="utf-8", errors="replace").splitlines():
            if re.match(r"^(error|!)", ln):
                out.append(ln.strip())
    except OSError:
        pass
    return out[:8]


def run_build(d, since=None):
    """Build main.pdf. Returns a dict for the browser."""
    t0 = time.time()
    with BUILD_LOCK:
        pdf = d / "main.pdf"
        # watch.sh or the editor's LaTeX extension may already be rebuilding
        # because main.tex changed. Let that build finish instead of racing it.
        if since is not None:
            time.sleep(1.3)
            waited = False
            quiet = 0
            deadline = time.time() + 240
            while time.time() < deadline:
                if other_builders(d):
                    waited, quiet = True, 0
                else:
                    quiet += 1
                    if not waited or quiet >= 3:
                        break
                time.sleep(0.4)
            if waited:
                ok = mtime(pdf) >= since
                return {"ok": ok, "by": "watcher",
                        "seconds": round(time.time() - t0, 1),
                        "errors": [] if ok else (log_errors(d) or ["The other build did not produce a new PDF."]),
                        "warnings": []}
        exe = tectonic()

        def go(args):
            with open(d / "build.log", "w") as log:
                try:
                    return subprocess.run([exe] + args, cwd=str(d), stdout=log,
                                          stderr=subprocess.STDOUT, timeout=600).returncode == 0
                except FileNotFoundError:
                    log.write("error: tectonic was not found on PATH\n")
                    return False
                except subprocess.TimeoutExpired:
                    log.write("error: the build took longer than ten minutes\n")
                    return False
        ok = go(FAST) or go(FULL)
        if ok:
            try:
                log = (d / "main.log").read_text(encoding="utf-8", errors="replace")
            except OSError:
                log = ""
            if re.search(r"undefined (references|citations)|Rerun to get", log):
                go(FULL)
        errors = [] if ok else (log_errors(d) or ["The build failed; see build.log."])
        subprocess.run(["chflags", "hidden", "main.aux", "main.bbl", "main.blg",
                        "main.log", "main.out", "main.synctex.gz", "build.log"],
                       cwd=str(d), capture_output=True)
        warnings = []
        check = ROOT / "check_all.py"
        if ok and os.access(check, os.X_OK):
            try:
                out = subprocess.run([str(check), str(d)], capture_output=True,
                                     text=True, timeout=120).stdout
                warnings = [ln for ln in out.splitlines()
                            if ln.strip() and not ln.startswith("ok")][:12]
            except (OSError, subprocess.SubprocessError):
                pass
        return {"ok": ok, "by": "editor", "seconds": round(time.time() - t0, 1),
                "errors": errors, "warnings": warnings}


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------

LAST_SAVE = {}     # paper -> (backup path, hash of the file after the save)
STATIC = {".html": "text/html; charset=utf-8", ".js": "text/javascript",
          ".mjs": "text/javascript", ".css": "text/css",
          ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
          ".pdf": "application/pdf", ".svg": "image/svg+xml",
          ".gif": "image/gif", ".woff2": "font/woff2", ".woff": "font/woff",
          ".ttf": "font/ttf"}
FIG_EXT = (".pdf", ".png", ".jpg", ".jpeg", ".gif", ".svg")


def doc_payload(name):
    d = paper_dir(name)
    src = read_tex(d)
    doc = Doc(src, Lookup(d))
    return {"paper": name, "hash": digest(src), "mtime": mtime(d / "main.tex"),
            "pdf_mtime": mtime(d / "main.pdf"), "items": doc.items,
            "can_comment": "reviewnote" in doc.lookup.macros,
            "macros": {"\\" + k: v[1] for k, v in doc.lookup.macros.items()
                       if len(v[1]) < 2000 and "\\begin" not in v[1]},
            "can_revert": LAST_SAVE.get(name, (None, None))[1] == digest(src)}


class Handler(BaseHTTPRequestHandler):
    server_version = "ManuscriptEditor/1"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass

    # -- plumbing --------------------------------------------------------
    def _send(self, code, body, ctype="application/json"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False).encode("utf-8")
        elif isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _local(self):
        """Refuse requests that did not come from this page on this machine."""
        host = (self.headers.get("Host") or "").split(":")[0]
        if host not in ("127.0.0.1", "localhost"):
            return False
        origin = self.headers.get("Origin")
        if origin:
            o = urlparse(origin)
            if o.hostname not in ("127.0.0.1", "localhost") or o.port != self.server.server_address[1]:
                return False
        return True

    def _file(self, path, ctype=None):
        try:
            data = path.read_bytes()
        except OSError:
            return self._send(404, {"error": "not found"})
        self._send(200, data, ctype or STATIC.get(path.suffix.lower(), "application/octet-stream"))

    # -- GET -------------------------------------------------------------
    def do_GET(self):
        if not self._local():
            return self._send(403, {"error": "forbidden"})
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        try:
            if u.path in ("/", "/index.html"):
                return self._file(HERE / "index.html")
            if u.path.startswith("/vendor/"):
                p = (HERE / "vendor" / u.path[len("/vendor/"):]).resolve()
                if (HERE / "vendor").resolve() not in p.parents:
                    return self._send(404, {"error": "not found"})
                return self._file(p)
            if u.path == "/api/papers":
                return self._send(200, {"papers": [
                    {"name": n, "group": n.rpartition("/")[0], "label": n.rpartition("/")[2].replace("_", " "),
                     "pdf": (ROOT / n / "main.pdf").exists()} for n in papers()],
                    "default": self.server.default_paper})
            if u.path == "/api/doc":
                return self._send(200, doc_payload(q.get("paper", "")))
            if u.path == "/api/stat":
                d = paper_dir(q.get("paper", ""))
                return self._send(200, {"mtime": mtime(d / "main.tex"),
                                        "pdf_mtime": mtime(d / "main.pdf")})
            if u.path == "/api/pdf":
                return self._file(paper_dir(q.get("paper", "")) / "main.pdf")
            if u.path == "/api/fig":
                d = paper_dir(q.get("paper", "")).resolve()
                rel = q.get("path", "")
                for base in (d, d / "figures"):
                    for ext in ("",) + FIG_EXT:
                        p = (base / (rel + ext)).resolve()
                        if d in p.parents and p.suffix.lower() in FIG_EXT and p.is_file():
                            return self._file(p)
                return self._send(404, {"error": "not found"})
        except KeyError:
            return self._send(404, {"error": "no such paper"})
        except Exception as exc:    # keep the server alive, show the reason
            return self._send(500, {"error": "%s: %s" % (type(exc).__name__, exc)})
        self._send(404, {"error": "not found"})

    # -- POST ------------------------------------------------------------
    def do_POST(self):
        if not self._local() or "application/json" not in (self.headers.get("Content-Type") or ""):
            return self._send(403, {"error": "forbidden"})
        try:
            n = int(self.headers.get("Content-Length") or 0)
            if n < 0 or n > MAX_BODY:
                self.close_connection = True
                return self._send(413, {"error": "request too large"})
            req = json.loads(self.rfile.read(n).decode("utf-8")) if n else {}
            if not isinstance(req, dict):
                raise ValueError("not an object")
            d = paper_dir(req.get("paper", ""))
        except KeyError:
            return self._send(404, {"error": "no such paper"})
        except (ValueError, UnicodeDecodeError):
            return self._send(400, {"error": "bad request"})
        try:
            if self.path == "/api/save":
                return self._save(d, req)
            if self.path == "/api/build":
                since = req.get("since")
                since = float(since) if isinstance(since, (int, float)) else None
                return self._send(200, {"build": run_build(d, since)})
            if self.path == "/api/revert":
                return self._revert(d, req)
        except Exception as exc:
            return self._send(500, {"error": "%s: %s" % (type(exc).__name__, exc)})
        self._send(404, {"error": "not found"})

    def _save(self, d, req):
        edits = req.get("edits") or []
        saved_at = None
        if edits:
            with FILE_LOCK:
                text = read_tex(d)
                new, why = plan_save(text, edits, Lookup(d))
                if new is None:
                    return self._send(409, {"error": why})
                if new != text:
                    if read_tex(d) != text:
                        return self._send(409, {"error": "main.tex changed while saving; nothing was written, try again"})
                    path = backup(d, text)
                    saved_at = time.time()
                    write_tex(d, new)
                    if read_tex(d) != new:
                        return self._send(500, {"error": "main.tex did not read back as written; the previous version is in " + str(path)})
                    LAST_SAVE[paper_key(d)] = (path, digest(new))
        build = run_build(d, saved_at) if req.get("build", True) else None
        self._send(200, {"saved": len(edits), "saved_at": saved_at, "build": build})

    def _revert(self, d, req):
        with FILE_LOCK:
            path, after = LAST_SAVE.get(paper_key(d), (None, None))
            text = read_tex(d)
            if not path or digest(text) != after:
                return self._send(409, {"error": "main.tex has changed since that save, so it was not reverted"})
            with open(path, "r", encoding="utf-8", newline="") as f:
                old = f.read()
            backup(d, text)
            saved_at = time.time()
            write_tex(d, old)
            LAST_SAVE.pop(paper_key(d), None)
        build = run_build(d, saved_at) if req.get("build", True) else None
        self._send(200, {"saved_at": saved_at, "build": build})


def main():
    ap = argparse.ArgumentParser(description="Word-style editor for the manuscripts.")
    ap.add_argument("paper", nargs="?", help="folder name of the paper to open first")
    ap.add_argument("--port", type=int, default=8377)
    ap.add_argument("--no-open", action="store_true", help="do not open the browser")
    ap.add_argument("--root", help="folder that holds the papers (default: the folder above the editor)")
    a = ap.parse_args()
    if a.root:
        global ROOT
        ROOT = Path(a.root).expanduser().resolve()
        if not ROOT.is_dir():
            sys.exit("No such folder: %s" % ROOT)
    if a.paper:
        a.paper = a.paper.rstrip("/")
        if a.paper not in papers():      # a paper inside published/ etc. can be given by its own name
            same = [n for n in papers() if n.rpartition("/")[2] == a.paper]
            if len(same) == 1:
                a.paper = same[0]
        if a.paper not in papers():
            sys.exit("No paper called %r. Papers: %s" % (a.paper, ", ".join(papers())))
    url = "http://127.0.0.1:%d/" % a.port
    if a.paper:
        url += "?paper=" + urllib.request.quote(a.paper)
    try:
        srv = ThreadingHTTPServer(("127.0.0.1", a.port), Handler)
    except OSError:
        # Probably already running: just open it.
        try:
            urllib.request.urlopen("http://127.0.0.1:%d/api/papers" % a.port, timeout=3).read()
        except Exception:
            sys.exit("Port %d is in use by something else; try --port 8378." % a.port)
        print("Editor already running at " + url)
        if not a.no_open:
            webbrowser.open(url)
        return
    srv.daemon_threads = True
    srv.default_paper = a.paper
    print("Manuscript editor: " + url + "   (Ctrl-C to stop)")
    if not a.no_open:
        threading.Timer(0.4, webbrowser.open, [url]).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print()


if __name__ == "__main__":
    main()
