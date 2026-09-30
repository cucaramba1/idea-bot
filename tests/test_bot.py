from ideabot.bot import selected_names


def test_selected_names_stale_after_restart_returns_none():
    picks: dict = {}
    assert selected_names(picks, 5) is None


def test_selected_names_returns_selection_in_order_and_pops():
    picks = {5: (["A", "B", "C"], {2, 0})}
    assert selected_names(picks, 5) == ["A", "C"]
    assert 5 not in picks
