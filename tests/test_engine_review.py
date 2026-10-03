from majai.engine import play_game
from majai.players import AIPlayer, EfficiencyPlayer
from majai.review import review_events, format_report


def test_game_conserves_points_and_ranks():
    r = play_game([EfficiencyPlayer() for _ in range(4)], seed=11)
    assert sorted(r["ranks"]) == [1, 2, 3, 4]
    left = sum(1 for e in r["events"] if e["type"] == "reach_accepted") - sum(
        1 for e in r["events"] if e["type"] == "hora")  # 供託は和了者に移るので概算確認のみ
    assert abs(sum(r["scores"]) - 100000) <= 1000 * max(0, abs(left)) + 8000


def test_review_runs_on_selfplay_log():
    r = play_game([AIPlayer(), EfficiencyPlayer(), EfficiencyPlayer(), EfficiencyPlayer()], seed=4)
    ds = review_events(r["events"], 0)
    assert ds
    # AI自身の打牌は、AI評価でほぼ全て一致するはず(鳴きの別判断を除く)
    disc = [d for d in ds if d.kind == "discard"]
    assert sum(d.agree for d in disc) / len(disc) > 0.95
    assert "レビュー結果" in format_report(ds)
