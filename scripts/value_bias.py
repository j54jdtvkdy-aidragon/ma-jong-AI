"""打点見積り(agent.estimate_han → han_to_points)と、実際の和了点(役判定)のずれを測る。
聴牌の局面ごとに、待ち牌それぞれで実際に和了した場合の点数の平均と、AIの見積りを比べる。"""
import os, random, sys
from collections import defaultdict
from multiprocessing import Pool
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from majai.agent import estimate_han, han_to_points
from majai.engine import play_game
from majai.params import Params
from majai.players import AIPlayer, EfficiencyPlusPlayer
from majai.tiles import str_to_idx


def run(seed):
    rng = random.Random(seed)
    players = [AIPlayer(), EfficiencyPlusPlayer(), AIPlayer(Params()), EfficiencyPlusPlayer()]
    rng.shuffle(players)
    rows = []

    def hook(rd, trackers):
        if rng.random() > 0.3:
            return
        for p in range(4):
            waits = rd.waits(p)
            if not waits:
                continue
            st = trackers[p]
            vals = []
            for w in waits:
                tile = next((t for t in range(w * 4, w * 4 + 4)), None)
                r = rd.hand_value(p, tile, False, False)
                if r:
                    vals.append(r.cost["main"] + (r.cost["additional"] if False else 0))
            if not vals:
                continue
            counts = st.hand_counts()
            menzen = st.is_menzen()
            han, sure = estimate_han(st, counts, st.melds[p], menzen, False)
            if menzen and st.riichi[p]:
                han += 1.0 + 0.5
            elif menzen:
                han += 1.0
            est = han_to_points(han, p == rd.oya)
            rows.append((menzen, st.riichi[p], p == rd.oya, est, sum(vals) / len(vals), len(vals) / len(waits)))
    play_game(players, seed=seed, hook=hook)
    return rows


if __name__ == "__main__":
    with Pool(4) as pool:
        rows = [r for rs in pool.map(run, range(30000, 30300)) for r in rs]
    print("聴牌サンプル", len(rows), "(役なしで和了できない待ちは除外)")
    groups = defaultdict(list)
    for men, rch, oya, est, act, frac in rows:
        key = ("立直" if rch else ("ダマ(門前)" if men else "副露")) + ("・親" if oya else "・子")
        groups[key].append((est, act))
    for k in sorted(groups):
        g = groups[k]
        e = sum(x[0] for x in g) / len(g)
        a = sum(x[1] for x in g) / len(g)
        mae = sum(abs(x[0] - x[1]) for x in g) / len(g)
        corr_num = sum((x[0] - e) * (x[1] - a) for x in g)
        corr_den = (sum((x[0] - e) ** 2 for x in g) * sum((x[1] - a) ** 2 for x in g)) ** 0.5
        print(f"{k:12s} n={len(g):5d} 見積り平均={e:6.0f} 実際の平均={a:6.0f} (比={e / a:.2f}) 平均絶対誤差={mae:5.0f} 相関={corr_num / corr_den if corr_den else 0:.2f}")
