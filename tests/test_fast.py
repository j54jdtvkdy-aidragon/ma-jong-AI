import random
import pytest
from majai import shanten as S
from majai.params import Params
from majai.players import EfficiencyPlusPlayer, AIPlayer
from majai.engine import play_game

pytestmark = pytest.mark.skipif(S._lib is None, reason="C extension unavailable")


def _rand_hand(rng, n):
    pool = [i for i in range(34) for _ in range(4)]
    rng.shuffle(pool)
    c = [0] * 34
    for t in pool[:n]:
        c[t] += 1
    return c


def test_c_matches_python_shanten_with_melds():
    rng = random.Random(3)
    for _ in range(1500):
        mel = rng.choice([0, 0, 1, 2, 3, 4])
        c = _rand_hand(rng, rng.choice([13, 14]) - 3 * mel)
        assert S.shanten(c, mel) == S.shanten_py(c, mel)


def test_invalid_counts_do_not_crash():
    c = [0] * 34
    c[0] = 5
    assert S.shanten(c, 0) == 9


def test_discard_table_matches_python_fallback():
    rng = random.Random(4)
    for _ in range(60):
        c = _rand_hand(rng, 14)
        vis = [x + rng.choice([0, 0, 1]) for x in c]
        vis = [min(4, v) for v in vis]
        fast = S.discard_table(c, 0, vis)
        lib, S._lib = S._lib, None
        try:
            slow = S.discard_table(c, 0, vis)
        finally:
            S._lib = lib
        assert fast == slow


def test_params_roundtrip_and_players(tmp_path):
    p = Params(danger_scale=1.7, kan_enable=0)
    f = tmp_path / "p.json"
    p.to_json(f)
    assert Params.load(f) == p
    r = play_game([AIPlayer(p), EfficiencyPlusPlayer(), EfficiencyPlusPlayer(), EfficiencyPlusPlayer()], seed=5)
    assert sorted(r["ranks"]) == [1, 2, 3, 4]
