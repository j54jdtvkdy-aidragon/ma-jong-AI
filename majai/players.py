"""対局用プレイヤー。discard / call の2メソッドを持つ。"""
import random
from .agent import (choose_discard, choose_call, choose_self_kan, choose_daiminkan, threats, tile_danger,
                    estimate_han, call_options, deal_in_prob, evaluate_call)
from .params import Params
from .shanten import shanten
from .tiles import str_to_idx, is_red


class AIPlayer:
    """期待値ベースのAI。params を差し替えて別の打ち筋にできる(省略時は調整済みの最良設定)。"""
    name = "majai"

    def __init__(self, params=None):
        self.params = params or Params.best()

    def discard(self, st, forbidden=()):
        st.params = self.params
        c = choose_discard(st, forbidden)
        return {"tile": c["tile"], "riichi": c["riichi"]}

    def kan(self, st):
        st.params = self.params
        return choose_self_kan(st)

    def call(self, st, actor, tile):
        st.params = self.params
        k = choose_daiminkan(st, actor, tile)
        if k:
            return k
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
                if counts[x] >= 4:
                    continue
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

    def kan(self, st):
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

    def kan(self, st):
        return None


class KanHappyPlayer(EfficiencyPlayer):
    """テスト用: カンできるときは常にカンする(エンジンのカン処理の網羅確認)。"""
    name = "kanhappy"

    def kan(self, st):
        from .agent import self_kan_options
        o = self_kan_options(st)
        return o[0] if o else None

    def call(self, st, actor, tile):
        from .agent import call_options
        for o in call_options(st, actor, tile):
            if o["type"] == "daiminkan":
                return o
        for o in call_options(st, actor, tile):
            if o["type"] == "pon":
                return o        # 加槓の機会を作るためポンもする
        return None


class EfficiencyPlusPlayer:
    """強化した素朴bot: 牌効率 + 役牌ポン + 確実な役のある仕掛け + 立直者への基本的な防御。
    (AIの期待値モデルは使わず、昔ながらのルールで動く。比較・練習相手用)"""
    name = "efficiency+"

    def _table(self, st, forbidden):
        from .shanten import discard_table
        counts = st.hand_counts()
        vis = st.visible_counts()
        return counts, vis, discard_table(counts, len(st.melds[st.me]), vis)

    def discard(self, st, forbidden=()):
        counts, vis, tb = self._table(st, forbidden)
        ths = threats(st)
        dora = set(st.dora_indices())
        best, fold_best = None, None
        for t in set(st.hand):
            i = str_to_idx(t)
            if i in forbidden:
                continue
            s, u, _ = tb[i]
            keep = (1 if i in dora else 0) + (1 if is_red(t) else 0) \
                + (1 if i >= 31 or i in (27 + "ESWN".index(st.bakaze), 27 + "ESWN".index(st.seat_wind)) else 0)
            key = (s, -u, keep)
            if best is None or key < best[0]:
                best = (key, t, s)
            if ths:
                d = deal_in_prob(st, i, vis, ths)
                fk = (d, s, -u, keep)
                if fold_best is None or fk < fold_best[0]:
                    fold_best = (fk, t)
        riichi = best[2] == 0 and st.is_menzen() and not st.riichi[st.me] and st.scores[st.me] >= 1000 \
            and st.tiles_left >= 4
        if ths and best[2] >= 1 and fold_best is not None:
            return {"tile": fold_best[1], "riichi": False}      # 聴牌していなければ降りる
        if ths and best[2] == 0 and fold_best is not None:
            if deal_in_prob(st, str_to_idx(best[1]), vis, ths) > 0.12:   # 危険牌は押さない
                return {"tile": fold_best[1], "riichi": False}
        return {"tile": best[1], "riichi": bool(riichi)}

    def kan(self, st):
        return None

    def call(self, st, actor, tile):
        if st.riichi[st.me] or threats(st) or st.tiles_left < 4:
            return None
        opts = [o for o in call_options(st, actor, tile) if o["type"] != "daiminkan"]
        if not opts:
            return None
        idx = str_to_idx(tile)
        yakuhai = idx >= 31 or idx in (27 + "ESWN".index(st.bakaze), 27 + "ESWN".index(st.seat_wind))
        from .shanten import shanten
        counts = st.hand_counts()
        mn = len(st.melds[st.me])
        s0 = shanten(counts, mn)
        for o in opts:
            if o["type"] == "pon" and yakuhai:
                return o
        # 既に役が確定している手(役牌ポン済み/タンヤオ形)なら、シャンテンが進む鳴きをする
        if not st.is_menzen() or all(not (i >= 27 or i % 9 in (0, 8)) for i in range(34) if counts[i]):
            for o in opts:
                c = evaluate_call(st, actor, tile, o)
                if c is not None and c.shanten < s0:
                    han, sure = estimate_han(st, counts, st.melds[st.me], False, False)
                    if sure:
                        return o
        return None
