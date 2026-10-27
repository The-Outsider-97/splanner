# SPlanner integration and reuse audit — SLAI-v.2.4

**Audit date:** 2026-10-27

**Source branch:** `SLAI-v.2.4`

**Scope:** The `applications/` tree as found on disk (`Reader`, `scical`, `assets`, and the new `tools`), `src/utils/sldc.py` and
`src/utils/configuration.py`. Implementation status for SPlanner 1.0.

The rule this audit enforces: **SPlanner implements only what is specific to planning.** Everything another SLAI application
also needs lives once, in `applications/tools`, and is imported; nothing is copied.

## Reuse map

| Capability | Canonical implementation | SPlanner decision | Status |
| --- | --- | --- | --- |
| SLDC serialization | `src/utils/sldc.py` (`new_package`, `make_block`, `read_package`, `write_package`, `extract_text`) | Imported through `tools.slai.sldc()`; no copy of the format. Every note and every sticky is a `.sldc` file | **Integrated, round-trip tested** |
| SLDC package editing | `src/utils/sldc.py` supports many blocks, assets, signatures, comments | The editor handles one unsigned text block. A richer package opens read-only with an explanation; saving it is refused with 409 rather than flattening it | **Restricted, tested** |
| Configuration | `src/utils/configuration.py` (`ConfigRepository`) | `tools.config.load_section` reads `configs/config.yaml` through it and names a bad key in its error | **Integrated** |
| Loopback server, token and cookie handshake, Host/Origin/marker checks, CSP, error envelope | New `applications/tools/server` (the pattern Reader and SciCal already follow, written once) | SPlanner has no server code of its own; `routes.py` only declares routes | **Shared; SPlanner migrated** |
| Errors, validation, UTC instants, opaque ids | `applications/tools/{errors,validation}.py` | Used for every request field; the original 0.1 helpers were removed | **Shared** |
| Atomic writes, data folders, JSON preferences | `applications/tools/storage.py` | Preferences (`prefs.json`) use `Prefs`; note files are written by SLDC's own atomic writer | **Shared** |
| SQLite, WAL, migrations, backups | `applications/tools/database.py` | `store.py` declares only its tables and two migrations; transactions, `user_version`, the pre-migration copy and the online backup API are the kit's | **Shared** |
| Web kit: DOM builder, API client, dialogs, menus, toasts, icons, appearance controls | `applications/tools/web/src` | One import point (`src/client/kit.ts`); no `innerHTML`, no second dialog or toast implementation. SPlanner adds only planning glyphs through `registerGlyph` | **Shared** |
| Visual language | `applications/tools/web/slai-ui.css`: tokens, three themes, controls; `data-skin="soft"` for SPlanner | `styles.css` contains layout only and uses the kit's tokens, so light, dark and high-contrast all follow | **Shared; verified in all three themes** |
| Client build | `applications/tools/build/build-static.mjs` (same as `Reader/tools/build-static.mjs` and `scical/tools/build-static.mjs`, plus the shared kit) | `npm run build` | **Shared** |
| Logo, alert sounds | `splanner/assets/`, with `applications/assets` as the fallback behind it | Served at `/assets/...`; nothing copied into `dist/` | **Integrated** |
| Reader annotation UI and persistence | `Reader/src/client/{annotate-ui,ink}.ts`, `Reader/src/core/strokes.ts`, `Reader/slai_reader/annotations/` | A shared, versioned component or bridge must exist first | **Not integrated** |
| Reader hand-off | `Reader/slai_reader/interop.py` (`slai-handoff/1`) allows `writer` and `documaster` | An explicit `splanner` target needs a tested cross-application change | **Not implemented** |
| SLAI Writer | Source not present in this tree | Never invent a hand-off target | **Not implemented** |
| Planning, optimization, privacy, safety, verification agents | `src/agents/*` | Read-only proposals only after a schema and consent contract | **Deferred** |
| Calendar persistence | No canonical planner service exists | Local SQLite; single user | **Integrated locally** |
| Collaboration, identity, sync | None verified | Omitted on purpose | **Deferred** |

## Duplication check

Run on the SPlanner sources (excluding tests and `dist/`), searching for what the kit already provides:

* no `http.server`, `BaseHTTPRequestHandler`, `tempfile`, `os.replace`, `sqlite3.connect` or `yaml` in any SPlanner module;
* no `fetch(`, `XMLHttpRequest`, `innerHTML`, `debounce` or dialog/toast/menu implementation in `src/client` — each is imported from
  `kit.ts`;
* every `ApiError`, validation message, dialog, menu, toast, switch, segmented control and swatch on screen is the kit's.

SPlanner-only code, and why it cannot be shared: calendar arithmetic and time zones (`src/core/dates.ts`), overlap layout
(`layout.ts`), task grouping (`tasks.ts`), reminder timing (`reminders.ts`), the time-grid with drag and resize
(`src/client/timegrid.ts`), and the store of events, tasks, notes, reminders and stickies. A future application that needs a
calendar would import these; they are written without anything specific to the page, but they stay in SPlanner until a second
application actually asks for them.

## What the shared kit changed for the other applications

Nothing. Reader and SciCal were **not modified**. They still carry their own copies of: `tools/build-static.mjs`, `dom.ts` and
`ui.ts` (Reader), `api.ts` (SciCal), the token/cookie/Origin/CSP handling in each Python service, and their appearance controls.
They can move to the kit one module at a time, each with its own tests as the safety net. Until then the *duplication is in
those two applications*, not in SPlanner.

## Architectural constraints

Runtime ports: Documaster 8766, Reader 8767, SciCal 8768, SPlanner 8769, GangUP 8765. SPlanner depends on the shared tools and on
SLAI's `src.utils`; it does not import Reader or SciCal, so it cannot break either of them. The upgrade from the 0.1 database is
done in place with a backup first.

## Evidence

| Check | Result |
| --- | --- |
| `python -m unittest discover -s applications/splanner/tests -t .` | **94 passed** (store, repeat engine, SLDC round trips, upgrades from 0.1 and schema 2, API with the real server) |
| `python -m unittest discover -s applications/tools/tests -t .` | **44 passed** |
| TypeScript: shared kit | **13 passed**; `tsc` strict, no errors |
| TypeScript: SPlanner core | **27 passed** in UTC, Europe/Amsterdam, America/Los_Angeles, Asia/Kolkata and Pacific/Auckland; `tsc` strict, no errors |
| Real-browser tests (Chromium 1286×724 and 390×800) | **99 passed**; every browser console error fails a test |
| Visual comparison | The Today view was rendered at the size of the reference screenshot and compared; Calendar, Tasks, Notes and Board checked in light, dark and high-contrast |

Repeats are implemented once, in `recurrence.py`; the TypeScript side only names presets and describes a rule, and asks the server
for occurrences, so there is no second date-expansion to drift. ICS export writes `RRULE`/`EXDATE`/`RECURRENCE-ID` with `TZID`
but no `VTIMEZONE` block, and other calendars treat a day-29/30/31 monthly repeat differently from SPlanner's "last day of
the month" (they skip the month).

Defects found by the browser tests and fixed: several quick key presses on a sticky's resize handle redrew the note from an
earlier save and ended at the wrong size; a sticky's text lost its line breaks (and was cut at 160 characters) after a reload
because the board read the list excerpt; menus opened from inside a dialog appeared behind it; moving an event with held arrow
keys sent a stale version and conflicted with itself; a brand-new data folder showed an "upgraded" notice.

## Not verified

* Windows and macOS runs (the browser tests ran on Linux; the code avoids platform-specific calls, and `tzdata` is a Windows
  requirement).
* Screen-reader behaviour with a real screen reader (landmarks and names are asserted, speech output is not).
* Firefox and Safari. Chromium only.
* Behaviour with very large data sets beyond the limits stated in the README.

## Integration acceptance gate

Do not mark Reader/Writer interoperability complete until real bidirectional open/hand-off flows, revision mismatch detection,
permissions, annotation round-trip and ownership semantics pass integration tests. Do not mark SLAI agent participation complete
until real agent construction, configuration, consent, safe proposal validation and an offline fallback have been exercised with
the real v2.4 interfaces. Do not move Reader or SciCal onto the shared kit without running their own full suites.
