"""Interval helpers."""


def _norm(intervals):
    return sorted(intervals)


def merge(intervals):
    """Merge overlapping/touching half-open [start, end) intervals; sorted by start."""
    if not intervals:
        return []
    out = []
    for start, end in sorted(intervals):
        if out and start <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], end))
        else:
            out.append((start, end))
    return out
    """Merge overlapping/touching half-open [start, end) intervals; sorted by start."""
    if not intervals:
        return []
    out = []
    for start, end in _norm(intervals):
        if out and start < out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], end))
        else:
            out.append((start, end))
    return out
