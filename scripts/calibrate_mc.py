"""モンテカルロ和了率の較正: 自己対戦の実際の和了率と、(ron_rate, hazard) の組ごとの予測を比べ、
対数損失が最小の組を探す。  python scripts/calibrate_mc.py --games 40"""
import argparse, itertools, math, os, sys, random
from multiprocessing import Pool
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from majai.engine import play_game
from majai.players import AIPlayer
from majai import shanten as S
from majai.state import StateTracker
from majai.tiles import str_to_idx

GRID = list(itertools.product([0.05, 0.1, 0.2, 0.35], [0.0, 0.02, 0.04, 0.07, 0.1]))


class Rec(AIPlayer):
    """AIPlayerと同じ打牌をしつつ、各判断時点の予測材料を記録する。"""
    def __init__(self):
        super().__init__()
        self.rows = []     # (round_key, seat, shanten, menzen, draws, preds{grid:(pt,pr)})

    def discard(self, st, forbidden=()):
        out = super().discard(st, forbidden)
        t = out["tile"]
        counts = st.hand_counts()
        mn = len(st.melds[st.me])
        counts[str_to_idx(t)] -= 1
        s = S.shanten(counts, mn)
        if s <= 2 and not out["riichi"] and not st.riichi_accepted[st.me]:
            vis = st.visible_counts()
            unseen = [max(0, 4 - v) for v in vis]
            furi = [0] * 34
            for d in st.discards[st.me]:
                furi[str_to_idx(d.tile)] = 1
            furi[str_to_idx(t)] = 1
            draws = max(1, st.tiles_left // 4)
            preds = []
            for rr, hz in GRID:
                pt, pr = S.mc_win(counts, mn, unseen, furi, draws, 60, 7 + len(self.rows), 0, rr, hz)
                preds.append(min(1.0, pt + pr))
            self.rows.append(((st.bakaze, st.kyoku, st.honba), st.me, s, st.is_menzen(), draws, preds))
        return out


def one(seed):
    players = [Rec() for _ in range(4)]
    r = play_game(players, seed=seed)
    winners = {}
    cur = None
    for e in r["events"]:
        if e["type"] == "start_kyoku":
            cur = (e["bakaze"], e["kyoku"], e["honba"])
            winners[cur] = set()
        if e["type"] == "hora":
            winners[cur].add(e["actor"])
    rows = []
    for p in players:
        for key, seat, s, men, draws, preds in p.rows:
            rows.append((s, men, draws, seat in winners.get(key, set()), preds))
    return rows


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--games", type=int, default=40)
    a = ap.parse_args()
    with Pool(4) as pool:
        rows = [r for rs in pool.map(one, range(5000, 5000 + a.games)) for r in rs]
    rows = [r for r in rows if r[1]]          # 役の問題を避けるため門前のみで較正
    print("判断数(門前)", len(rows))
    for s in (0, 1, 2):
        sub = [r for r in rows if r[0] == s]
        if sub:
            print(f"shanten={s}: n={len(sub)} 実際の和了率={sum(r[3] for r in sub) / len(sub):.3f}")
    best = None
    for gi, (rr, hz) in enumerate(GRID):
        ll = 0.0
        for s, men, draws, win, preds in rows:
            p = min(0.97, max(0.01, preds[gi]))
            ll -= math.log(p if win else 1 - p)
        means = [sum(r[4][gi] for r in rows if r[0] == s) / max(1, sum(1 for r in rows if r[0] == s)) for s in (0, 1, 2)]
        print(f"ron_rate={rr:<5} hazard={hz:<5} logloss={ll / len(rows):.4f} 予測平均(s=0,1,2)={[round(m, 3) for m in means]}")
        if best is None or ll < best[0]:
            best = (ll, rr, hz)
    print("最良:", best)
