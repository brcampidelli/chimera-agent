"""Frozen synthetic retrieval items for S30-55."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Item:
    id: str
    query: str
    answer: str
    relevant_turn: str


ITEMS = tuple(
    Item(
        f"q{i:02d}",
        f"archive {topic}",
        f"Decision {i}: {answer}.",
        f"turn-{i:02d}",
    )
    for i, (topic, answer) in enumerate(
        [
            ("cedar", "keep the cedar index"), ("marble", "use a marble cache"),
            ("harbor", "move harbor logs weekly"), ("violet", "retain violet labels"),
            ("copper", "rotate copper keys monthly"), ("lantern", "ship lantern tests"),
            ("meadow", "keep meadow snapshots"), ("rocket", "use rocket deploys"),
            ("saffron", "store saffron settings"), ("piano", "avoid piano polling"),
            ("walnut", "prefer walnut fixtures"), ("island", "archive island reports"),
            ("velvet", "keep velvet themes"), ("comet", "retry comet jobs once"),
            ("bamboo", "use bamboo partitions"), ("silver", "rotate silver backups"),
            ("orchid", "retain orchid metrics"), ("canyon", "compact canyon events"),
            ("pepper", "keep pepper migrations"), ("sailboat", "use sailboat probes"),
            ("granite", "pin granite schemas"), ("lemon", "remove lemon retries"),
            ("pocket", "keep pocket receipts"), ("tulip", "use tulip fixtures"),
            ("cloud", "store cloud cursors"), ("cobalt", "retain cobalt reports"),
            ("maple", "prefer maple indexes"), ("planet", "ship planet benchmarks"),
            ("quartz", "rotate quartz tokens"), ("river", "keep river snapshots"),
        ]
    )
)
