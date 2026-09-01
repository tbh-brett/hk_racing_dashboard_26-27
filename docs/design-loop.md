# Changing how it looks

You design in Claude Design. The dashboard ships from `web/`. This is how a
change gets from one to the other, in both directions, and what catches it when
the two drift apart.

---

## The short version

| You want to | Do this |
|---|---|
| Redesign a page | Change the artboard in Claude Design → **Send to Claude Code Web** → ask for it to be ported |
| Tweak a colour, a size, a spacing | Ask directly. It is one token in `tokens.css`, no round trip |
| See what the design says and the build does not | `python -m pytest tests/test_conformance.py -q` |
| Show Claude Design what the build looks like now | Paste a screenshot, or ask for artboards generated from the live pages |

---

## Design → dashboard

**Use "Send to Claude Code Web" from Claude Design.** It seeds the project
into the session's workspace, which is the only path that carries the artboards
themselves rather than a picture of them. Pasting a screenshot works and is
often enough for a small change, but a screenshot cannot be diffed and the
conformance test cannot read it.

The exports live in `web/design-source/*.dc.html`, kept verbatim. **They are
never edited by hand** — they are the record of what was designed, and the
moment someone edits one to match the build, the test comparing them is
comparing a thing to itself.

When new artboards land, this is the first thing to run:

```powershell
.\.venv\Scripts\python -m pytest tests/test_conformance.py -q
```

It reads every column each artboard declares and fails on any the built page
does not render. That is how the missing draw and jockey on Trials were found,
and it now covers all eight pages — an artboard it cannot read is a failure,
not a skip, because a check that cannot see a page looks exactly like a page
with nothing wrong.

A design and a build are allowed to disagree. The disagreement goes in
`DIVERGENCES` in that test file, with the reason, or it is indistinguishable
from an omission.

## Dashboard → design

Claude Design cannot read this repo. Two ways to close that direction:

1. **A screenshot.** Fine for "this is too cramped" or "the tags are the wrong
   colour". Fastest, and what you have been doing.
2. **Artboards generated from the live pages.** Ask for it: the current
   production page is rendered and written out as a `.dc.html` artboard you can
   open in Claude Design and edit. Worth it when the build has moved on and you
   want to design *from where it actually is* rather than from the August
   export.

---

## What not to round-trip

Some things are cheaper to say than to redesign.

**Anything in `tokens.css`.** Every colour, size and spacing step in the whole
dashboard is a token in one file, and no page may contain a raw hex — a test
enforces that. "Make the form guide bigger" was one line: `--fs` from 12px to
13px, and every page grew together. A round trip through the canvas would have
changed one page and left the other seven behind.

**Column order and column content.** Faster to say "put weight next to the
draw" than to redraw the table.

**Anything about behaviour.** Sort order, what a hover says, what a click does.
The artboards carry sample data, so a canvas cannot express "this is empty when
the horse did not place".

---

## Why the exports are kept at all

They are not documentation. `tests/test_conformance.py` reads them on every
test run, so they are executable: a column that exists in the design and not in
the build fails the suite. Delete them and that check silently becomes a check
of nothing.

Their format is Claude Design's `<x-dc>` template dialect rather than plain
HTML — `sc-for`, `sc-if`, `{{ }}` bindings, rendered by `support.js` on React.
See `web/design-source/README.md`. Opening one in a browser needs React and
Babel, which the canvas loads from a CDN.
