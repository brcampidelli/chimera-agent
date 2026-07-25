from ranges import merge

def test_touching_intervals_merge():
    # half-open: [1,3) and [3,5) are contiguous and must become one
    assert merge([(1, 3), (3, 5)]) == [(1, 5)]
    # Test for intervals that are not contiguous but should not merge
    assert merge([(1, 3), (4, 5)]) == [(1, 3), (4, 5)]
    # Test for intervals that are overlapping
    assert merge([(1, 4), (3, 5)]) == [(1, 5)]
    # Test for intervals that are completely contained
    assert merge([(1, 6), (2, 4)]) == [(1, 6)]
    # Test for intervals that are not overlapping and should not merge
    assert merge([(1, 2), (3, 4)]) == [(1, 2), (3, 4)]
    # Test for a single interval
    assert merge([(2, 2)]) == [(2, 2)]
    # Test for an empty list
    assert merge([]) == []


    # half-open: [1,3) and [3,5) are contiguous and must become one
    assert merge([(1, 3), (3, 5)]) == [(1, 5)]

def test_overlap_and_containment():
    assert merge([(1, 6), (2, 4)]) == [(1, 6)]
    assert merge([(5, 7), (1, 3)]) == [(1, 3), (5, 7)]

def test_empty_and_single():
    assert merge([]) == []
    assert merge([(2, 2)]) == [(2, 2)]
