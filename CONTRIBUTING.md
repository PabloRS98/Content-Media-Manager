# Contributing

Thanks for considering a contribution.

## Setup

```bash
git clone https://github.com/<your-user>/Content-Media-Manager.git
cd Content-Media-Manager
pip install -r requirements-dev.txt
cp .env.example .env
uvicorn app.main:app --reload --port 8000
```

Run the test suite with `pytest` and the linter with `ruff check app tests migrations`.
CI also requires 80 % coverage (`pytest --cov=app --cov-fail-under=80`).

## Guidelines

- Dependencies live in `requirements.in` / `requirements-dev.in`. After
  changing one, regenerate the hashed lock files with the `pip-compile`
  command written at the top of that `.in` file and commit both; CI and the
  Docker image install with `--require-hashes`.
- Keep it dependency-light: no frontend build step, no paid APIs, no services
  beyond SQLite + the container itself.
- New metadata sources must have a free tier and must be optional — the app
  has to keep working with every key left empty.
- Match the existing structure: `routers/` for endpoints, `services/` for
  business logic and external API clients, `templates/` for Jinja2 views.
- Schema changes go through an **Alembic migration** in
  `migrations/versions/` — see [Database changes](#database-changes). Never a
  change that requires dropping the database.
- Open an issue for anything non-trivial before writing code, so we can agree
  on the approach first.

## Database changes

The models in `app/models.py` are the source of truth, and every change to
them needs a migration. The app applies pending migrations on startup
(`init_db()`, also run by the container entrypoint), so whatever you write
runs unattended on every existing install.

1. Change the model.
2. Generate a draft against a database at the current head:

   ```bash
   DB_PATH=/tmp/dev.db alembic upgrade head
   DB_PATH=/tmp/dev.db alembic revision --autogenerate -m "what changes"
   ```

   `DB_PATH=/tmp/dev.db alembic check` must say "No new upgrade operations
   detected" once your migration is applied.

3. **Read and edit the draft.** Autogenerate misses data moves, renames
   (it sees a drop plus an add) and SQLite quirks: use
   `op.batch_alter_table()` to alter or drop columns and constraints. If data
   has to move, do it inside the migration, and make `downgrade()` put it
   back. Don't import app code from a migration — copy what you need, so the
   migration keeps describing what happened that day
   (`e5f6a7b80005_generos_a_tabla.py` is a worked example).
4. Run `pytest tests/test_migraciones.py`:
   `test_el_esquema_de_las_migraciones_coincide_con_los_modelos` fails if the
   migrations and the models disagree.

`ensure_columns()` in `app/database.py` is **not** the way to change the
schema: it only exists to bring databases from before Alembic (1.0.0) up to
the first revision.

## Releases

`latest` follows `main`. A release adds image tags that don't move, so users
can choose when to take new migrations.

1. Pick the version ([SemVer](https://semver.org/)): a migration that
   reshapes existing data, or any change that needs action on upgrade, is at
   least a minor bump and gets an "Upgrade notes" line in the changelog.
2. In one commit on `main`: set `version` in `pyproject.toml`, and rename
   `## [Unreleased]` in `CHANGELOG.md` to `## [X.Y.Z] - YYYY-MM-DD` (with a
   new empty `[Unreleased]` above it).
3. Once CI is green on that commit, tag it and push the tag:

   ```bash
   git tag -a vX.Y.Z -m "vX.Y.Z"
   git push origin vX.Y.Z
   ```

   The tag runs the whole CI again and, only if every job passes, publishes
   `ghcr.io/pablors98/content-media-manager:X.Y.Z` and `:X.Y`.
4. Create the GitHub release from the tag, pasting that version's changelog
   section.

## Pull requests

- Keep PRs focused on one change.
- Describe what changed and why in the PR description.
- `pytest` and `ruff check app tests migrations` must pass. CI runs them on every PR, along with
  `pip-audit`, a smoke test of the hardened image and a migration from a 1.0.0
  database; the Docker image is only published when all of them pass.
- Add a test with your change. If you are fixing something listed in
  `tests/test_fallos_conocidos.py`, the test is already written: remove its
  `xfail` marker instead of writing a new one.
