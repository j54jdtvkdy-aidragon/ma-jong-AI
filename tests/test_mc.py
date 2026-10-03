import pytest
from majai import shanten as S

pytestmark = pytest.mark.skipif(S._lib is None, reason="C extension unavailable")


def _cnt(s):
    c = [0] * 34
    for t in s.split():
        c["mps".index(t[1]) * 9 + int(t[0]) - 1] += 1
    return c


def _unseen(h):
    return [4 - x for x in h]


def test_mc_is_deterministic_and_ordered():
    one = _cnt("1m 2m 3m 4p 5p 6p 7s 8s 9s 2m 3m 5p 7p")        # 1シャンテン
    tenpai = _cnt("1m 2m 3m 4p 5p 6p 7s 8s 9s 2m 3m 5p 5p")     # 聴牌(2m 3m 待ち)
    a = S.mc_win(one, 0, _unseen(one), [0] * 34, 12, 400, 1, 0, 0.2, 0.04)
    assert a == S.mc_win(one, 0, _unseen(one), [0] * 34, 12, 400, 1, 0, 0.2, 0.04)
    b = S.mc_win(tenpai, 0, _unseen(tenpai), [0] * 34, 12, 400, 1, 0, 0.2, 0.04)
    assert sum(b) > sum(a) > 0


def test_mc_furiten_blocks_ron_and_fixed_hand_cannot_improve():
    tenpai = _cnt("1m 2m 3m 4p 5p 6p 7s 8s 9s 2m 3m 5p 5p")
    furi = [0] * 34
    furi[0] = furi[3] = 1                                          # 待ち(1m/4m)を自分で切っている
    free = S.mc_win(tenpai, 0, _unseen(tenpai), [0] * 34, 12, 600, 2, 1, 0.5, 0.0)
    fu = S.mc_win(tenpai, 0, _unseen(tenpai), furi, 12, 600, 2, 1, 0.5, 0.0)
    assert fu[1] == 0 and free[1] > 0                               # フリテンならロン不可
    assert sum(fu) < sum(free)                                      # フリテンだと和了の合計は減る
