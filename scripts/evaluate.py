"""自己対戦で平均順位を測る。
  python scripts/evaluate.py --games 200 --hero majai --vs efficiency+
  python scripts/evaluate.py --games 200 --hero majai --vs mix        # 3人は素朴bot/強い素朴bot/AI既定からランダム
  python scripts/evaluate.py --params path.json ...                   # heroのパラメータを指定
"""
import argparse, os, random, sys, time
from multiprocessing import Pool
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from majai.engine import play_game
from majai.params import Params
from majai.players import AIPlayer, EfficiencyPlayer, EfficiencyPlusPlayer, RandomPlayer


def make(name, params=None):
    if name == "majai":
        return AIPlayer(params)
    if name == "majai-default":
        return AIPlayer(Params())
    return {"efficiency": EfficiencyPlayer, "efficiency+": EfficiencyPlusPlayer, "random": RandomPlayer}[name]()


def opponents(vs, rng):
    if vs == "mix":
        return [make(rng.choice(["efficiency", "efficiency+", "majai-default"])) for _ in range(3)]
    return [make(vs) for _ in range(3)]


def run(args):
    seed, hero, vs, params = args
    rng = random.Random(seed)
    seat = seed % 4                     # 席を回して有利不利を均す
    players = opponents(vs, rng)
    players.insert(seat, make(hero, Params.from_dict(params) if params else None))
    r = play_game(players, seed=seed)
    return r["ranks"][seat], r["scores"][seat]


def evaluate(games, hero, vs, params=None, seed0=0, procs=4, pool=None):
    jobs = [(seed0 + i, hero, vs, params) for i in range(games)]
    res = pool.map(run, jobs, chunksize=2) if pool else Pool(procs).map(run, jobs, chunksize=2)
    ranks = [r for r, _ in res]
    n = len(ranks)
    avg = sum(ranks) / n
    var = sum((r - avg) ** 2 for r in ranks) / max(n - 1, 1)
    return {"n": n, "avg_rank": avg, "se": (var / n) ** 0.5,
            "dist": [ranks.count(k) / n for k in (1, 2, 3, 4)],
            "avg_score": sum(s for _, s in res) / n}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--games", type=int, default=100)
    ap.add_argument("--hero", default="majai")
    ap.add_argument("--vs", default="efficiency+")
    ap.add_argument("--procs", type=int, default=4)
    ap.add_argument("--seed0", type=int, default=0)
    ap.add_argument("--params")
    a = ap.parse_args()
    t = time.time()
    params = None
    if a.params:
        import json
        params = json.load(open(a.params))
    r = evaluate(a.games, a.hero, a.vs, params, a.seed0, a.procs)
    print(f"{a.hero} vs 3x{a.vs}: games={r['n']} avg_rank={r['avg_rank']:.3f}±{r['se']:.3f} "
          f"dist(1-4)={[round(x, 3) for x in r['dist']]} avg_score={r['avg_score']:.0f} time={time.time() - t:.0f}s")
