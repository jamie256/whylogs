import numpy as np
import pandas as pd
import pytest

from whylogs.core import ColumnProfile
from whylogs.core.statistics.hllsketch import HllSketch
from whylogs.proto import ColumnMessage, ColumnSummary, InferredType
from whylogs.util.protobuf import message_to_dict

from ...helpers.testutil import compare_frequent_items


def test_all_numeric_types_get_tracked_by_number_tracker():
    all_values = [
        [1.0, 2.0, 3.0],
        [1, 2, 3],
        np.arange(4),
        np.linspace(1, 2, 5),
        pd.Series(np.arange(3)),
        np.zeros(3, dtype=np.int32),
        np.zeros(3, dtype=np.int16),
    ]
    for values in all_values:
        c = ColumnProfile("test")
        for v in values:
            c.track(v)
        assert c.number_tracker.count == len(values)


def test_all_nulls_inferred_type_null():
    import numpy as np

    from whylogs.proto import InferredType

    Type = InferredType.Type
    c = ColumnProfile("col")
    data = [None, np.nan, None] * 3
    for val in data:
        c.track(val)
    summary: ColumnSummary = c.to_summary()
    assert summary.counters.null_count.value == 9
    assert summary.schema.inferred_type.type == Type.NULL


def test_mostly_nulls_inferred_type_not_null():
    import numpy as np

    from whylogs.proto import InferredType

    Type = InferredType.Type
    c = ColumnProfile("col")
    data = [None, np.nan, None] * 3 + ["not a null val!"]
    for val in data:
        c.track(val)
    summary = c.to_summary()
    assert summary.schema.inferred_type.type != Type.NULL


def test_frequent_items_do_not_track_nulls():
    import numpy as np

    data = [None, np.nan, None]
    c = ColumnProfile("col")
    for val in data:
        c.track(val)
    assert c.frequent_items.to_summary() is None
    assert c.frequent_items.is_empty()
    assert c.cardinality_tracker.is_empty()
    assert c.cardinality_tracker.to_summary() is None


def test_track():
    c = ColumnProfile("col")
    data = [1, 2, 3, "string 1", "string 2", "3", 4.0, "3.95", "3.95st", None, True]
    for val in data:
        c.track(val)
    nt = c.number_tracker
    assert nt.floats.count == 6
    assert nt.ints.count == 0
    assert nt.floats.min == 1.0
    assert nt.floats.max == 4.0

    assert c.counters.count == len(data)
    assert c.counters.true_count == 1
    assert c.schema_tracker.get_count(InferredType.Type.NULL) == 1


def test_protobuf():
    c = ColumnProfile("col")
    for val in [1, 2, 3]:
        c.track(val)
    msg = c.to_protobuf()
    c1 = ColumnProfile.from_protobuf(msg)
    assert c1.column_name == c.column_name == "col"
    assert hasattr(c1, "number_tracker")
    assert hasattr(c1, "string_tracker")
    assert c1.string_tracker.length is not None

    assert c1.string_tracker.length.count == 0
    assert len(c1.string_tracker.char_pos_tracker.character_list) == 56
    c1.to_protobuf()


def test_summary():
    c = ColumnProfile("col")
    for n in [1, 2, 3]:
        c.track(n)
    summary = c.to_summary()
    actual_val = message_to_dict(summary)
    expected_val = {
        "counters": {
            "count": "3",
        },
        "schema": {
            "inferredType": {"type": "INTEGRAL", "ratio": 1.0},
            "typeCounts": {"INTEGRAL": "3"},
        },
        "numberSummary": {
            "count": "3",
            "min": 1.0,
            "max": 3.0,
            "mean": 2.0,
            "stddev": 1.0,
            "isDiscrete": False,
            "histogram": {
                "start": 1.0,
                "end": 3.0000003,
                "counts": ["3"],
                "max": 3.0,
                "min": 1.0,
                "bins": [1.0, 3.0000003],
                "n": "3",
                "width": 0.0,
            },
            "quantiles": {
                "quantiles": [0.0, 0.01, 0.05, 0.25, 0.5, 0.75, 0.95, 0.99, 1.0],
                "quantileValues": [1.0, 1.0, 1.0, 1.0, 2.0, 3.0, 3.0, 3.0, 3.0],
            },
            "uniqueCount": {"estimate": 3.0, "upper": 3.0, "lower": 3.0},
        },
    }
    # Top-level unique count needs to be approximately equal
    expected_unique = {
        "estimate": 3.000000014901161,
        "lower": 3.0,
        "upper": 3.0001498026537594,
    }
    actual_unique = actual_val.pop("uniqueCount")
    assert actual_unique == pytest.approx(expected_unique, 0.0001)

    # Cannot do a straightforward frequentItems count since order is ambiguous
    actual_freq = actual_val.pop("frequentItems")
    assert set(actual_freq.keys()) == {"items"}
    expected = [("1", "1"), ("2", "1"), ("3", "1")]
    assert len(actual_freq["items"]) == len(expected)
    counts = []
    for v in actual_freq["items"]:
        counts.append((v["jsonValue"], v["estimate"]))
    assert set(counts) == set(expected)

    # Compare the messages, excluding the frequent numbers counters
    assert actual_val == expected_val


def test_merge():
    col = ColumnProfile("test")
    vals = [1, 1.0, "string", True, False, None]
    for v in vals:
        col.track(v)

    merged = col.merge(col)
    assert merged.counters.count == 12
    assert merged.schema_tracker.get_count(InferredType.Type.NULL) == 2
    assert merged.counters.true_count == 4
    assert merged.number_tracker.ints.count == 0
    assert merged.number_tracker.floats.count == 4
    assert merged.string_tracker.count == 2


def test_fallback_number_counter():
    col = ColumnProfile("test")
    vals = [1, 1.0, 2, 3, 4, 5, 6, 6.0, "text"]
    for v in vals:
        col.track(v)
    col.cardinality_tracker = HllSketch()

    summary = col.to_summary()
    assert summary.unique_count.estimate == summary.number_summary.unique_count.estimate


def test_fallback_string_counter():
    col = ColumnProfile("test")
    vals = ["a", "b", "c", "d", "e", "f", 1.0, 2.0]
    for v in vals:
        col.track(v)
    col.cardinality_tracker = HllSketch()

    summary = col.to_summary()
    assert summary.unique_count.estimate == summary.string_summary.unique_count.estimate


def test_fallback_fallbacks_to_number_counter():
    col = ColumnProfile("test")
    vals = ["a", "b", 1.0, 2.0]
    for v in vals:
        col.track(v)
    col.cardinality_tracker = HllSketch()

    summary = col.to_summary()
    assert summary.unique_count.estimate == summary.number_summary.unique_count.estimate


def test_copy_counters_null_count_in_schema_tracker():
    col = ColumnProfile("test")
    vals = ["a", "b", None, "d", pd.NA, "f", 1.0, 2.0]
    for v in vals:
        col.track(v)
    assert col.schema_tracker.get_count(InferredType.Type.NULL) == 2

    # ensuring we can still access the value in summary mode
    assert col.to_summary().counters.null_count.value == 2

    # Mimic a legal protobuf with null_count set
    msg: ColumnMessage = col.to_protobuf()
    msg.counters.null_count.value = 2

    roundtrip = ColumnProfile.from_protobuf(msg)
    assert roundtrip.schema_tracker.get_count(InferredType.Type.NULL) == 4
