"""対局用プレイヤー。discard / call の2メソッドを持つ。"""
import random
from .agent import choose_discard, choose_call, evaluate_discards, call_options
from .shanten import shanten
from .tiles import str_to_idx, is_red


class AIPlayer:
    name = "majai"

    def discard(self, st, forbidden=()):
        c = choose_discard(st, forbidden)
        return {"tile": c["tile"], "riichi": c["riichi"]}

    def call(self, st, actor, tile):
        r = choose_call(st, actor, tile)
        return r[0] if r else None


class EfficiencyPlayer:
    """比較用の素朴なbot: 受け入れ最大、聴牌即立直、鳴かない、押しっぱなし。"""
    name = "efficiency"

    def discard(self, st, forbidden=()):
        counts = st.hand_counts()
        mn = len(st.melds[st.me])
        best = None
        for t in set(st.hand):
            i = str_to_idx(t)
            if i in forbidden:
                continue
            counts[i] -= 1
            s = shanten(counts, mn)
            u = 0
            for x in range(34):
                counts[x] += 1
                if shanten(counts, mn) < s:
                    u += 4 - counts[x] + 1
                counts[x] -= 1
            counts[i] += 1
            key = (s, -u, is_red(t) * 1)
            if best is None or key < best[0]:
                best = (key, t, s)
        return {"tile": best[1], "riichi": best[2] == 0 and not st.melds[st.me]}

    def call(self, st, actor, tile):
        return None


class RandomPlayer:
    name = "random"

    def __init__(self, seed=0):
        self.rng = random.Random(seed)

    def discard(self, st, forbidden=()):
        ok = [t for t in st.hand if str_to_idx(t) not in forbidden]
        return {"tile": self.rng.choice(ok), "riichi": False}

    def call(self, st, actor, tile):
        return None
