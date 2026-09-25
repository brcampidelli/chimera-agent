# tally

A tiny personal ledger kept in one JSON file.

```
python -m tally.cli --ledger ledger.json add 2026-09-01 coffee 3.50 --category food
python -m tally.cli --ledger ledger.json report
python -m tally.cli --ledger ledger.json report --by-category
python -m tally.cli --ledger ledger.json export entries.csv
```

Amounts are stored as integer cents. Dates are ISO strings (`YYYY-MM-DD`).

Run the tests with `python -m pytest -q`.
