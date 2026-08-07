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
