# Changelog

All notable changes to this project are documented here. Format loosely
follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

## [Unreleased]

Second technical audit (August 2026). Findings are tracked by id (`MC-*`);
each entry below names the id it closes.

### Fixed

- **[MC-C1]** The `.env` file was read by a path relative to the process's
  working directory. Started from anywhere other than the repository root,
  pydantic-settings silently found nothing and the app came up with every
  default value — including `ENABLE_AUTH=false` and empty API keys — without
  raising anything. The path is now absolute, `/static` is mounted with a
  resolved path for the same reason, and starting without authentication now
  logs a warning naming the `.env` it tried to read.

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
