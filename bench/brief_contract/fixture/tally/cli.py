"""Command line for the ledger: `python -m tally.cli <command>`."""

import argparse
import csv
import sys

from tally import money, store


def _add_parser(sub):
    p = sub.add_parser("add", help="add an entry")
    p.add_argument("date")
    p.add_argument("description")
    p.add_argument("amount", type=float)
    p.add_argument("--category", default="misc")


def _report_parser(sub):
    p = sub.add_parser("report", help="print the total")
    p.add_argument("--by-category", action="store_true")


def _export_parser(sub):
    p = sub.add_parser("export", help="write the entries as CSV")
    p.add_argument("out")


def build_parser():
    parser = argparse.ArgumentParser(prog="tally")
    parser.add_argument("--ledger", default="ledger.json")
    sub = parser.add_subparsers(dest="command", required=True)
    _add_parser(sub)
    _report_parser(sub)
    _export_parser(sub)
    return parser


def cmd_add(args):
    entries = store.load(args.ledger)
    store.add_entry(entries, args.date, args.description, money.to_cents(args.amount), args.category)
    store.save(args.ledger, entries)
    print("added")


def cmd_report(args):
    entries = store.load(args.ledger)
    if args.by_category:
        for name, cents in store.by_category(entries).items():
            print(name, money.format_money(cents))
    else:
        print("Total:", money.format_money(store.total(entries)))


def cmd_export(args):
    entries = store.load(args.ledger)
    with open(args.out, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        for e in entries:
            writer.writerow([e["date"], e["description"], e["cents"], e["category"]])
    print("exported", len(entries))


COMMANDS = {"add": cmd_add, "report": cmd_report, "export": cmd_export}


def main(argv=None):
    args = build_parser().parse_args(argv)
    COMMANDS[args.command](args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
