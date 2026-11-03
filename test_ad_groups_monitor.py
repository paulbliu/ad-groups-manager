from ad_groups_monitor import diff_snapshots


def test_diff():
    old = {"A": ["x", "y"], "B": ["z"]}
    new = {"A": ["y", "w"], "B": ["z"], "C": ["q"]}
    assert diff_snapshots(old, new) == {
        "A": {"added": ["w"], "removed": ["x"]},
        "C": {"added": ["q"], "removed": []},
    }


def test_no_change():
    assert diff_snapshots({"A": ["x"]}, {"A": ["x"]}) == {}
