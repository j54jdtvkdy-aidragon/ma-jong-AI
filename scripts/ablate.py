"""戦術の有無を比較する。 python scripts/ablate.py --vs efficiency+ --games 800"""
import argparse, json, os, sys
from dataclasses import asdict
from multiprocessing import Pool
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))
from evaluate import evaluate
from majai.params import Params

VARIANTS = {
    "base(調整済)": {},
    "+立直バイアス": {"riichi_bias": 1500.0},
    "+強制オリ": {"fold_shanten": 1, "push_danger_limit": 0.12},
    "+役牌ポン/役確定の鳴き": {"yakuhai_pon_force": 1, "sure_yaku_call_force": 1},
    "+全部": {"riichi_bias": 1500.0, "fold_shanten": 1, "push_danger_limit": 0.12,
            "yakuhai_pon_force": 1, "sure_yaku_call_force": 1},
}

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--vs", default="efficiency+")
    ap.add_argument("--games", type=int, default=800)
    ap.add_argument("--seed0", type=int, default=500000)
    a = ap.parse_args()
    pool = Pool(4)
    base = asdict(Params.best())
    for name, delta in VARIANTS.items():
        r = evaluate(a.games, "majai", a.vs, {**base, **delta}, seed0=a.seed0, pool=pool)
        print(f"{name:24s} avg_rank={r['avg_rank']:.3f}±{r['se']:.3f} 1位率={r['dist'][0]:.3f} 4位率={r['dist'][3]:.3f}", flush=True)
