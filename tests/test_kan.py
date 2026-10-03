from collections import Counter
from majai.agent import self_kan_options, kan_judgement, call_options
from majai.engine import play_game
from majai.players import KanHappyPlayer, AIPlayer, EfficiencyPlayer
from majai.state import StateTracker
from majai.tiles import str_to_idx


class Checked(KanHappyPlayer):
    """手番ごとに、エンジンと視点トラッカーの枚数整合性を検証する。"""
    def discard(self, st, forbidden=()):
        assert len(st.hand) + 3 * len(st.melds[st.me]) == 14, (st.hand, st.melds[st.me])
        return super().discard(st, forbidden)


def _tracker(hand, melds=None):
    st = StateTracker(0)
    st.update({"type": "start_kyoku", "bakaze": "E", "kyoku": 1, "honba": 0, "kyotaku": 0, "oya": 0,
               "scores": [25000] * 4, "dora_marker": "9p",
               "tehais": [hand[:13], ["?"] * 13, ["?"] * 13, ["?"] * 13]})
    st.update({"type": "tsumo", "actor": 0, "pai": hand[13]})
    return st


def test_ankan_option_and_judgement():
    st = _tracker(["1m", "2m", "3m", "4p", "5p", "6p", "7s", "8s", "9s", "E", "E", "E", "C", "E"])
    # 東ははじめから4枚(手牌13枚目まで3枚+ツモ)
    opts = self_kan_options(st)
    assert [o["type"] for o in opts] == ["ankan"]
    assert kan_judgement(st, opts[0])[0] is True


def test_no_kan_when_riichi_threat():
    st = _tracker(["1m", "2m", "3m", "4p", "5p", "6p", "7s", "8s", "9s", "E", "E", "E", "C", "E"])
    st.update({"type": "reach", "actor": 2})
    st.update({"type": "dahai", "actor": 2, "pai": "N", "tsumogiri": False})
    st.update({"type": "reach_accepted", "actor": 2})
    ok, why = kan_judgement(st, self_kan_options(st)[0])
    assert ok is False and "立直" in why


def test_daiminkan_option():
    st = _tracker(["1m", "2m", "3m", "4p", "5p", "6p", "7s", "8s", "9s", "E", "E", "E", "C", "N"])
    st.update({"type": "dahai", "actor": 0, "pai": "N", "tsumogiri": True})
    st.update({"type": "dahai", "actor": 1, "pai": "E", "tsumogiri": False})
    assert "daiminkan" in [o["type"] for o in call_options(st, 1, "E")]


def test_engine_kan_flow_consistent():
    seen = Counter()
    for seed in range(14):
        r = play_game([Checked() for _ in range(4)], seed=seed)
        assert sorted(r["ranks"]) == [1, 2, 3, 4]
        for e in r["events"]:
            seen[e["type"]] += 1
        # 嶺上牌は kan の直後の tsumo / dora はkan回数以内
        kans = sum(seen[k] for k in ("ankan", "kakan", "daiminkan"))
    assert seen["ankan"] and seen["daiminkan"] and seen["kakan"], seen
    assert seen["dora"] <= kans


def test_ai_with_kan_selfplay_and_review():
    from majai.review import review_events
    for seed in range(6):
        r = play_game([AIPlayer(), EfficiencyPlayer(), EfficiencyPlayer(), EfficiencyPlayer()], seed=seed)
        review_events(r["events"], 0)
