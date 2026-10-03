"""自己対戦で平均順位を測る。 例: python scripts/evaluate.py --games 40 --vs efficiency"""
import argparse, os, sys, time, json
from multiprocessing import Pool
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from majai.engine import play_game
from majai.players import AIPlayer, EfficiencyPlayer, RandomPlayer

MAKERS = {"majai": AIPlayer, "efficiency": EfficiencyPlayer, "random": RandomPlayer}


def run(args):
    seed, hero, vs = args
    seat = seed % 4                     # 席を回して有利不利を均す
    players = [MAKERS[vs]() for _ in range(4)]
    players[seat] = MAKERS[hero]()
    r = play_game(players, seed=seed)
    return r["ranks"][seat], r["scores"][seat]


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--games", type=int, default=40)
    ap.add_argument("--hero", default="majai")
    ap.add_argument("--vs", default="efficiency")
    ap.add_argument("--procs", type=int, default=4)
    ap.add_argument("--seed0", type=int, default=0)
    a = ap.parse_args()
    t = time.time()
    with Pool(a.procs) as p:
        res = p.map(run, [(a.seed0 + i, a.hero, a.vs) for i in range(a.games)])
    ranks = [r for r, _ in res]
    n = len(ranks)
    dist = [ranks.count(k) / n for k in (1, 2, 3, 4)]
    print(f"{a.hero} vs 3x{a.vs}: games={n} avg_rank={sum(ranks)/n:.3f} "
          f"dist(1-4)={[round(x,3) for x in dist]} avg_score={sum(s for _,s in res)/n:.0f} time={time.time()-t:.0f}s")
