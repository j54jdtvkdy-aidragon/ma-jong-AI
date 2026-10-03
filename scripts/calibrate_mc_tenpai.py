"""聴牌時のロン率(ron_rate)の較正。立直宣言(固定手)とダマ(手替わりあり)を別々に評価する。"""
import itertools, math, os, sys
from multiprocessing import Pool
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from majai.engine import play_game
from majai.players import AIPlayer
from majai import shanten as S
from majai.tiles import str_to_idx

RR = [0.1, 0.2, 0.35, 0.5, 0.7, 0.85, 1.0]
HZ = 0.05


class Rec(AIPlayer):
    def __init__(self):
        super().__init__()
        self.rows = []

    def discard(self, st, forbidden=()):
        out = super().discard(st, forbidden)
        t = out["tile"]
        counts = st.hand_counts()
        mn = len(st.melds[st.me])
        counts[str_to_idx(t)] -= 1
        if S.shanten(counts, mn) == 0 and st.is_menzen():
            vis = st.visible_counts()
            unseen = [max(0, 4 - v) for v in vis]
            furi = [0] * 34
            for d in st.discards[st.me]:
                furi[str_to_idx(d.tile)] = 1
            furi[str_to_idx(t)] = 1
            draws = max(1, st.tiles_left // 4)
            fixed = 1 if out["riichi"] else 0
            preds = [S.mc_win(counts, mn, unseen, furi, draws, 80, 11 + len(self.rows), fixed, rr, HZ) for rr in RR]
            self.rows.append(((st.bakaze, st.kyoku, st.honba), st.me, fixed, [min(1, a + b) for a, b in preds]))
        return out


def one(seed):
    players = [Rec() for _ in range(4)]
    r = play_game(players, seed=seed)
    win, cur = {}, None
    for e in r["events"]:
        if e["type"] == "start_kyoku":
            cur = (e["bakaze"], e["kyoku"], e["honba"]); win[cur] = set()
        if e["type"] == "hora":
            win[cur].add(e["actor"])
    return [(fx, seat in win.get(k, set()), pr) for p in players for k, seat, fx, pr in p.rows]


if __name__ == "__main__":
    with Pool(4) as pool:
        rows = [r for rs in pool.map(one, range(7000, 7040)) for r in rs]
    for fx in (1, 0):
        sub = [r for r in rows if r[0] == fx]
        if not sub:
            continue
        print(f"{'立直(固定)' if fx else 'ダマ'}: n={len(sub)} 実際の和了率={sum(r[1] for r in sub) / len(sub):.3f}")
        for i, rr in enumerate(RR):
            ll = -sum(math.log(min(.97, max(.01, r[2][i])) if r[1] else 1 - min(.97, max(.01, r[2][i]))) for r in sub) / len(sub)
            print(f"   ron_rate={rr:<5} 予測平均={sum(r[2][i] for r in sub) / len(sub):.3f} logloss={ll:.4f}")
