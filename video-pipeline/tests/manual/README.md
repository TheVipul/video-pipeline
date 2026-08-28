# Manual smoke scripts

These are exploratory scripts, not tests. They print results for a human to
read and make live network calls to YouTube and the LLM. They were originally
in `tests/` named `test_*.py`, which made `pytest` try to collect them - it
found no `test_` functions or assertions and reported **"no tests ran"**,
so the project appeared to have a test suite while actually having none.

Renamed to `smoke_*` and moved here so pytest ignores them. The real,
assertion-based suite lives in `tests/`.

Run one directly:

    .venv/bin/python tests/manual/smoke_metadata.py
