# LaTeX editor

A Word-style editor for LaTeX papers: editable prose on the left, the compiled PDF on the right, one button to save into `main.tex` and recompile. Python standard library only, plus a vendored copy of pdf.js. Listens on 127.0.0.1.

```
python3 server.py                          # papers in the folder above this one
python3 server.py PAPER_FOLDER             # open a given paper
python3 server.py --root ~/papers          # papers somewhere else
python3 server.py --port 8378 --no-open
```

A *paper* is any sub-folder of the root that has a `main.tex`. Builds use `tectonic` (on `PATH` or `/opt/homebrew/bin`).

## Desktop app (macOS)

```
app/build_app.sh                 # makes ~/Desktop/Manuscript Editor.app
app/build_app.sh /Applications   # or put it somewhere else
```

A native window (Swift and the system's WebKit). It needs no browser and no internet: everything, including pdf.js and KaTeX, is served from this folder on 127.0.0.1. Double-click it: it starts `server.py` if it is not already running (log in `~/Library/Logs/manuscript-editor.log`) and stops it again on quit. If you have unsaved edits, quitting asks first. The app points at this checkout, so rebuild it after moving the folder. Building needs `swiftc` (Xcode command line tools); running does not.

The repository holds no papers: `.gitignore` refuses `.tex`, `.bib`, `.pdf` and `.docx` files except the made-up test paper in `tests/fixtures`.

## Using it

- Click and type like in Word. Bold and italic (⌘B, ⌘I) work. Enter inside a paragraph or bullet starts a new one (not inside bold, italic or a footnote).
- Citations, references, maths and other LaTeX show as small chips. They move and delete as one unit and cannot be typed into. Hover for the source.
- **Save & recompile** (⌘S) writes only the paragraphs you changed into `main.tex`. The page is frozen only for the moment of writing; the compile runs while you keep typing.
- Typed `% & _ # $ { } \ ~ ^` are escaped for you.
- Click the PDF to jump to that paragraph. Click a paragraph to see where it is in the PDF.
- A failed build shows the LaTeX error and leaves the last good PDF in place. **Undo last save** restores the previous `main.tex`.
- **Comments.** Every `\reviewnote{who}{tag}{text}` shows as a small marker in the text and is listed in the **Comments** panel (toolbar button). Click a comment in the panel to jump to it. Click a marker to read, edit or delete it. To add one, click in a paragraph or bullet (or select text) and press **Comment** (⌥⌘M); it is saved as `\reviewnote{Your name}{date}{text}`, which the house preamble turns into a PDF comment. Replies written as `| REPLY (Name): ...` show on their own line. LaTeX `%` comments show as grey markers and are kept as they are.
- **Bold, italic, underline, superscript, subscript** follow Word's rule: if everything selected already has the format, the button removes it; otherwise it adds it.
- Not editable here: tables, figures (captions are), equations, front matter, the reference list. Tick "Show LaTeX blocks" to see them.

## Why it cannot damage a paper

Every save goes through the same checks on the server before a byte is written. If any check fails, nothing is written and your edits stay on the page.

1. **Only real paragraphs.** Each edit must replace a paragraph that the server itself finds in the current `main.tex`. If the file changed underneath, the paragraph is found again by its exact text, or the save is refused.
2. **It reads back as what you saw.** The page sends both the LaTeX and a description of what it showed (words, chips, bold or italic). The server re-reads the LaTeX and requires the same thing. This catches the cases where typed text would change meaning in LaTeX: text after a trailing `% comment`, `[` or `*` after `\\` or `\item`, and a letter after `\textmu`. The page inserts the guards (`{}` or a newline) itself; the check makes sure it did.
3. **Balanced, and nothing new.** The new LaTeX must have balanced braces and maths and no `\begin`/`\end`. It may contain only chips that were already in that paragraph, plus the editor's own `\textbf{` and friends, paragraph breaks, `\item`, and new comments of exactly the form `\reviewnote{name}{tag}{plain text}` (only in paragraphs and bullets, only if the paper defines `\reviewnote`). Paragraph breaks are refused inside a wrapper and in headings and captions.
4. **Neighbours untouched.** After splicing, the whole file is parsed again, and every paragraph you did not edit must come back exactly as it was.
5. **Safe writes.** One save at a time (lock); the file is re-read just before writing and the save is refused if it changed. The write is atomic (temp file, fsync, rename) and then read back. A copy of the previous file goes to `.backups/<paper>/` (the last 60 are kept; git-ignored).

If `watch.sh` or another editor is also building the paper, the editor waits for that build instead of racing it.

## Tests

```
python3 test_roundtrip.py --fixture      # load + save with no edits is byte-identical
python3 tests/test_edits.py --fixture    # every awkward edit, through the page's own serialiser
python3 test_roundtrip.py                # the same on your real papers (reads only)
python3 tests/test_edits.py
```

`tests/test_edits.py` needs `node`. It runs the serialiser copied out of `index.html` on a small fake DOM, applies edits around every chip and wrapper of every paragraph (`[`, `*`, letters, spaces, deleting chips, emptying bold, paragraph breaks, new bold), and requires that the server accepts every honest edit. It also tries hand-made damage (unbalanced braces, new commands, `\end{document}`, LaTeX that does not match what the page showed) and requires that each one is refused. Neither test writes to any paper.
