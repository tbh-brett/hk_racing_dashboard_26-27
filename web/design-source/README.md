# design-source/

The Claude Design canvas export, kept verbatim as the reference for `web/pages/`.
**Do not edit these.** They are the record of what was designed; the ported pages
are what ships.

## Format

These are not plain HTML. They are templates in Claude Design's `<x-dc>` format,
rendered at runtime by `support.js` (`dc-runtime`, generated — marked do-not-edit)
on top of React:

| Construct | Count | Meaning |
|---|---|---|
| `sc-for list="{{ x }}" as="y"` | 278 | repeat over a collection |
| `sc-if value="{{ x }}"` | 264 | conditional render |
| `{{ expr }}` | — | interpolation |
| `style-hover="..."` | 108 | hover styling (inline styles cannot express `:hover`) |
| `onClick="{{ fn }}"` | 112 | handler binding |

The `{{ }}` bindings read directly off a props object, so the data shape is already
separated from the markup — that maps cleanly onto the JSON `hkrd/api/` will serve.

## Pages

Eight artboards: Race Day, Form Guide, Lookup, Bets, Blackbook, Results, Trials,
Model Analysis. Model Analysis is the Lab content from design brief 05 §5, which
settles where it lives — it is its own nav item, making the nav eight, not seven.

`Briefing.dc.html` came later (2026-09-23), as the front page: what the tipsters
back, what was said, and the tote against the bookmakers' fixed odds. It was
exported as one self-contained file; this copy is that file unpacked, with its
runtime reference pointed at `./support.js` like the others and nothing else
changed. It proposed two tokens, `--tip` and `--voice`, now in `tokens.css`.

It was redesigned on 25 Sep 2026 around one endpoint, `/api/briefing`, and
this file is that second artboard. It loads four sample answers from
`./briefing-samples/` when opened outside the bundle; those are real tipster
and bookmaker text, so they are kept off this public repo, on the owner's PC
in `Claude outputs/briefing-design/`.
