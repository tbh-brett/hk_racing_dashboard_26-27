# web/

The Claude Design output, served statically by FastAPI.

- `pages/` — one file per page: Race Day, Form Guide, Lookup, Bets, Blackbook, Results, Trials
- `assets/` — shared CSS and JS

Rules, from `AGENTS.md`:

- No Python here, and no database access. This layer talks to `hkrd/api/` over `fetch`.
- One colour, one meaning, app-wide.
- Tabular numerals on every numeric column.
- Never a bare number — every figure carries context and a sample size.
- Race times render `m:ss.xx`; sectional splits stay plain seconds.
- Running style gets four distinct hues, not brightness steps, and sorts
  Leader → On-Pace → Midfield → Closer.

Design briefs 09 §1 and 10 specify one shared hover/flyout component with viewport-fixed
positioning, collision detection and portal rendering. Build it once here and reuse it —
the twitching bug in the previous build came from positioning panels inside table flow.
