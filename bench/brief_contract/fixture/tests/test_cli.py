import json

from tally import cli


def test_add_then_report(tmp_path, capsys):
    ledger = tmp_path / "ledger.json"
    ledger.write_text(json.dumps([]), encoding="utf-8")
    cli.main(["--ledger", str(ledger), "add", "2026-09-01", "coffee", "3.5"])
    capsys.readouterr()
    cli.main(["--ledger", str(ledger), "report"])
    assert capsys.readouterr().out.strip() == "Total: $3.50"
