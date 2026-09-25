from tally import store


def test_add_entry():
    entries = []
    entry = store.add_entry(entries, "2026-09-01", "coffee", 350, "food")
    assert entries == [entry]
    assert entry == {"date": "2026-09-01", "description": "coffee", "cents": 350, "category": "food"}


def test_total_and_by_category():
    entries = [
        {"date": "2026-09-01", "description": "coffee", "cents": 350, "category": "food"},
        {"date": "2026-09-02", "description": "bus", "cents": 400, "category": "transport"},
        {"date": "2026-09-03", "description": "lunch", "cents": 1200, "category": "food"},
    ]
    assert store.total(entries) == 1950
    assert store.by_category(entries) == {"food": 1550, "transport": 400}


def test_save_and_load(tmp_path):
    path = tmp_path / "ledger.json"
    entries = [{"date": "2026-09-01", "description": "coffee", "cents": 350, "category": "food"}]
    store.save(path, entries)
    assert store.load(path) == entries
