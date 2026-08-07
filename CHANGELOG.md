# Changelog

All notable changes to this project are documented here. Format loosely
follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

## [Unreleased]

Second technical audit (August 2026). Findings are tracked by id (`MC-*`);
each entry below names the id it closes.

### Added

- **[MC-A5]** Security headers on every response: `Content-Security-Policy`,
  `X-Content-Type-Options`, `Referrer-Policy`. `img-src` stays open to `https:`
  on purpose — cover art comes from six different metadata APIs and the field is
  editable by hand, so a domain allowlist would silently break covers from any
  new source. A test pins that choice so nobody tightens it by accident.

### Fixed

- **[MC-C1]** The `.env` file was read by a path relative to the process's
  working directory. Started from anywhere other than the repository root,
  pydantic-settings silently found nothing and the app came up with every
  default value — including `ENABLE_AUTH=false` and empty API keys — without
  raising anything. The path is now absolute, `/static` is mounted with a
  resolved path for the same reason, and starting without authentication now
  logs a warning naming the `.env` it tried to read.
- **[MC-C2]** The container published its port on `0.0.0.0`, and nothing stopped
  the app from starting with the factory `admin`/`changeme` credentials — so the
  whole catalog could be readable and writable by anyone on the network with no
  indication that it was. The port now binds to loopback by default and is
  opened deliberately with `MEDIA_BIND`, and with `ENABLE_AUTH=true` the app
  refuses to start while `AUTH_PASSWORD` is the factory value or shorter than 8
  characters.

  **Upgrading:** if you reach this app from another machine, set
  `MEDIA_BIND=0.0.0.0` (behind a reverse proxy or a VPN) or the address of the
  specific interface you want to serve, and turn `ENABLE_AUTH` on.
- **[MC-C3]** A failed Google Books request wrote `GOOGLE_BOOKS_API_KEY` to the
  logs in the clear. The project has a module written specifically against this
  (`_logging_utils`), and the file even imports it — but the main handler used
  `logger.exception`, which dumps the raw exception, and that exception's
  message is the full request URL, key included. Now logged with `log_fallo_api`
  like everywhere else. Covered for TMDB and RAWG too, as a regression.
- **[MC-A7]** `.gitignore` had ten lines and `.dockerignore` five, so a stray
  virtualenv in the working tree (`.venv/` didn't match `.venv-audit/`) was one
  `git add -A` away from putting thousands of files and compiled binaries into
  the history, and was being uploaded to the Docker daemon on every build. Both
  files now cover virtualenvs, tool caches and database sidecar files, with the
  reasoning written down. Verified that none of it ever reached the history.
- **[MC-A1]** Paging past the first page dropped the active filter when the
  genre contained an `&` — `Sci-Fi & Fantasy` and `Action & Adventure` are real
  TMDB genres, and the pagination links were built by string concatenation with
  no URL encoding, so the `&` split the parameter in two. The links now go
  through `build_qs`, the same helper the filter buttons already used four lines
  above. Filter buttons still reset to page 1, which is the behaviour the helper
  exists to protect.
- **[MC-A4]** Telegram alerts were sent with `parse_mode: HTML` and titles
  interpolated raw, so anything called `Marley & Me` or `Will & Grace` came back
  as `400 Bad Request` — and the episode was marked as notified anyway, because
  the flag was set without checking whether the send worked. That alert was lost
  permanently and never retried. Titles and episode names are now escaped, the
  flag is only set on a successful send, and the bot token no longer reaches the
  logs (it travels in the URL path, and the failure was logged with the raw
  exception).
- **[MC-M6]** Deleting an item left dead rows in `list_items`: the relationship
  was declared only on the `Lista` side, so SQLAlchemy didn't know they existed
  from the item's side, and SQLite doesn't enforce foreign keys unless
  `PRAGMA foreign_keys=ON` (which this app doesn't set). Because SQLite reuses
  ids, a newly added item could inherit the deleted one's list memberships —
  a visible and very confusing bug. The inverse relationship is now declared,
  and startup sweeps any dead rows an existing database already carries.
- **[MC-M11]** `cover_url` was stored straight from the form without checking
  the URL scheme, and it ends up in an `<img src>`. Not exploitable today —
  Jinja escapes the attribute and `javascript:` doesn't run in an image source —
  but the field is hand-editable and auto-filled from six APIs, so the day it
  appears in an `<a href>` it becomes an XSS, and this is where the value is
  written. Only absolute `http(s)` URLs are accepted now.

- **[MC-M1]** The schema had exactly one declared index while the app filtered
  or sorted on nine columns without one, so the home page's eight status queries
  were eight full table scans per visit. Six indexes are now created at startup,
  chosen by measuring `EXPLAIN QUERY PLAN` against a copy of a real database
  rather than by guessing: six of eight representative queries go from `SCAN` to
  `SEARCH … USING INDEX`, and the catalog's default sort no longer needs a temp
  B-tree. `cover_url` was measured and deliberately left unindexed.
- **[MC-A2]** The IMDb CSV importer ran one `SELECT` per row to check for
  duplicates. A "Your Ratings" export can easily be 2 000–5 000 rows, so that
  was thousands of sequential queries inside a single HTTP request — long enough
  for a reverse proxy in front to time out. The existing ids are now preloaded
  once, the same way the Goodreads and Backloggd importers already did it.
  Measured: 202 read queries for a 200-row file, now 2.
- **[MC-A8]** The one-off v2 column backfill ran in full on *every* startup —
  its docstring said "once", but what was idempotent was the result, not the
  execution. One of its two queries is a `LIKE '%…%'` over a Text column, which
  can't use an index at all, so a large catalog paid for a full scan plus a
  substring search per row on every `docker compose restart`, before the
  healthcheck could even answer. It's now recorded as done in a small `app_meta`
  table — which is also the first step towards knowing what schema version a
  database is on.
- **[MC-M9]** The episode-alert job issued one extra query per episode: its
  `join` filtered but didn't load the relationship, so every title read hit the
  database again. Measured: 21 queries for 20 pending episodes, now 2.
- **[MC-M5]** The home page loaded every pending item — full rows, `overview`
  Text column and all — in order to sort them in Python and show twelve. Same
  for the "coming up" strip, which fetched every future episode and every dated
  wishlist entry to show six. Sorting and limiting now happen in SQL.
  `/calendario` still fetches everything, because that view genuinely needs it.
- **[MC-A3]** There were two "fill in missing covers" endpoints doing the same
  work and only one of them had been fixed: the import page's still ran the
  whole batch inside the HTTP request — 21 seconds at minimum, minutes with slow
  APIs — and didn't check whether a batch was already running, so it could be
  started alongside the other one and burn twice the free API quota. Both now
  share one lock and run in the background. The import page also gained a status
  strip that refreshes itself, so you can finally tell when a batch has
  finished; the old message said "come back in a minute and reload".
- **[MC-A9]** Adding an item made its metadata HTTP calls inside the POST. For a
  TMDB series that's one details call plus **one call per season** — 37 requests
  in a row for a 36-season show, each with a 10 second timeout, with the browser
  waiting. If a proxy timed out first you saw an error while the work carried on
  behind it and the final commit never ran, leaving the item created but without
  episodes. Enrichment now runs in the background, and seasons are fetched in
  parallel. Measured on a real 39-season series: the response takes 186 ms and
  885 episodes arrive afterwards.
- **[MC-M8]** The daily series refresh re-fetched *every* season of *every*
  followed series, including ones that finished years ago and will never change:
  with 30 series averaging 5 seasons that's 180 sequential requests, up to half
  an hour of job. It now asks only for seasons that can still bring news, runs
  four series at a time, and commits per series so a failure halfway through
  doesn't discard the work already done. The job also declares
  `max_instances=1`, so a container restart can no longer overlap two runs.
- **[MC-M7]** Saving an item queried the database once per tag, and tags were
  never deleted: removing the last "documentary" from every item left the row
  behind forever, so the table only ever grew and any future tag cloud or
  autocomplete would offer tags nobody uses. One query now, and unused tags are
  swept after each save.
- **[MC-M10]** The stats page's "collection by decade" chart pulled one row per
  catalog item into Python just to count them. SQLite groups them now. Genres
  still aggregate in Python because `genres` is a comma-separated string rather
  than a relation — that's the underlying problem, and normalising it is its own
  piece of work.

- **[MC-M4]** Real database migrations, with Alembic. The old `ensure_columns`
  could only ever `ADD COLUMN` — no indexes, no type changes, and no record of
  what version a database was on. It survives only to reconcile a pre-Alembic
  database, which `init_db` now detects, completes and stamps automatically
  before migrating. Migrations run in the container entrypoint before uvicorn
  starts, not inside the app: `alembic upgrade` there could sit waiting on a
  SQLite lock and hang startup without saying why.
- **[MC-M4]** `/salud` now answers 503 when the schema is out of date or a probe
  query fails, so Docker marks the container unhealthy instead of accepting a
  live process that returns 500 on every page.

### Security

- **[MC-X3]** Updated dependencies carrying **19 known vulnerabilities**: Jinja2
  3.1.4, python-multipart 0.0.12 and Starlette 0.38.6 (pulled in by FastAPI
  0.115.0). Found by running `pip-audit` for the first time. `pip-audit` now
  reports nothing.

### Changed

- **[MC-A6]** The CSRF origin check used to fail *open*: a state-changing
  request carrying neither `Sec-Fetch-Site` nor `Origin` was let through on
  purpose, so as not to break non-browser clients. It now fails closed and also
  accepts `Referer` as a fallback. A browser always sends at least one of the
  three on a POST, so the open case only ever helped scripts — which can add a
  header in one line — while leaving the door open to old webviews.

  **Upgrading:** if you POST to this app from a script, send an `Origin` or
  `Referer` header. If you reach the app under a name your reverse proxy
  doesn't set as `Host`, list it in the new `TRUSTED_ORIGINS`.

## [1.0.0] — 2026-08-03

First tagged release. The project existed and worked before this point, but
had no version discipline (no tags, no release notes) — this is where that
starts. Everything below shipped since the [initial technical
audit](docs/AUDITORIA.md), which is the closest thing to a "0.x" baseline.

### Added

- Standalone deployment: its own `docker-compose.yml`, project name, and
  named Docker volume, independent of any other app on the same host.
- A real `pytest` test suite, actually run in CI (previously CI only compiled
  the code and smoke-tested `/salud`).
- A language selector (ES/EN) for book search/cover-matching, so a Spanish
  edition doesn't come back with an English cover or title.
- HTTP Basic auth is now paired with lightweight CSRF protection
  (`Sec-Fetch-Site`/`Origin` checks) on every write route.
- A full visual redesign, "Archivo vintage": light and dark themes (dark by
  default, manual toggle, remembered per browser), aged-paper/leather-brown
  palette, serif titles, ratings as a stamped circular seal on the cover
  instead of a text line, bigger cover art, and the catalog's four filters
  (status/genre/duration/sort) collapsed into single-row dropdowns instead
  of a wall of always-open buttons.
- Four automatic, always-up-to-date list views (In progress / Pending /
  Completed / Wishlist) reachable from the home page and from the Lists tab
  — membership is computed live from each item's status, not stored, so an
  item moves in and out on its own as you update it.

### Fixed

- **Data loss / silent corruption**: renaming an item to match search results
  could silently collapse a saga's items into one (e.g. "Harry Potter and
  the Order of the Phoenix" losing its full title to a plain "Harry
  Potter"); movie/podcast duration mixups; the "search covers in background"
  progress counter; `completed_at` not being set when an item is added
  already completed instead of transitioning into it; duplicate rows on
  repeated IMDb CSV imports.
- **Wrong or unrelated covers**: a title/language match no longer accepts
  the first API result blindly — same-language, same-title candidates are
  now tie-broken by publication year, and a Wikipedia-sourced cover is
  rejected if the page's own text never mentions the item's author (fixes
  cases like *Seda* by Alessandro Baricco pulling in an unrelated "silk
  fabric" image).
- **Security**: a stored-XSS opening via an overridden `tojson` filter, an
  open redirect via the `Referer` header, third-party API keys leaking into
  application logs on request failure, and a blind SSRF via a
  user-suppliable podcast RSS URL (now resolved and checked against
  private/loopback/link-local ranges before the request goes out).
- **Visual bugs**: a literal "None" showing up in a few places, duplicate
  "special" episodes, a genre filter whose `%`/`_` wasn't escaped for SQL
  `LIKE` (so `?genero=%` returned the entire catalog), and out-of-range
  ratings silently corrupting the stats histogram.
- **Robustness**: a Google Books network failure returning a raw 500
  instead of "no results"; an N+1 query pattern on the stats page; batch
  cover enrichment moved off the request thread so it no longer risks a
  reverse-proxy timeout on a large catalog; capped file-upload size on CSV
  import; the container now runs as a non-root user and declares a
  `HEALTHCHECK`; `docker compose up` no longer aborts if `.env` was never
  created (every setting has a default).

See [docs/AUDITORIA.md](docs/AUDITORIA.md) for the original audit these
fixes trace back to, and the closed pull requests for the rest of the detail.
