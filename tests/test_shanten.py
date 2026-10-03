import random
from mahjong.shanten import Shanten
from majai.shanten import shanten


def test_matches_reference_library():
    rng = random.Random(1)
    ref = Shanten()
    for _ in range(400):
        pool = [i for i in range(34) for _ in range(4)]
        rng.shuffle(pool)
        n = rng.choice([13, 14])
        c = [0] * 34
        for t in pool[:n]:
            c[t] += 1
        assert shanten(c) == ref.calculate_shanten(c), c


def test_known():
    c = [0] * 34
    for i in (0, 1, 2, 3, 4, 5, 9, 10, 11, 18, 18, 19, 19):
        c[i] += 1
    assert shanten(c) == 0
