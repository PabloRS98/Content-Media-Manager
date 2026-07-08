**What this changes and why**

**Checklist**
- [ ] Tested locally (`docker compose up -d --build`, exercised the changed flow)
- [ ] Schema changes go through `ensure_columns()` in `app/database.py`, not a breaking migration
- [ ] No new required (non-optional) config or paid API
