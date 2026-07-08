# Contributing

Thanks for considering a contribution.

## Setup

```bash
git clone https://github.com/<your-user>/media-catalog.git
cd media-catalog
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --reload --port 8000
```

## Guidelines

- Keep it dependency-light: no frontend build step, no paid APIs, no services
  beyond SQLite + the container itself.
- New metadata sources must have a free tier and must be optional — the app
  has to keep working with every key left empty.
- Match the existing structure: `routers/` for endpoints, `services/` for
  business logic and external API clients, `templates/` for Jinja2 views.
- Schema changes go through `ensure_columns()` in `app/database.py`
  (additive `ALTER TABLE ADD COLUMN`, nullable or with a default) — never a
  change that requires dropping the database.
- Open an issue for anything non-trivial before writing code, so we can agree
  on the approach first.

## Pull requests

- Keep PRs focused on one change.
- Describe what changed and why in the PR description.
- Test manually against a local run before submitting (see Setup above);
  there is no CI test suite yet — a PR adding one is very welcome.
