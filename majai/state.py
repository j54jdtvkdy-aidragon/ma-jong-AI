"""mjaiイベント列から、あるプレイヤー視点の局面状態を復元する。
エンジンの対局もレビュー対象の牌譜も、同じ StateTracker を通して AI に渡る。"""
from dataclasses import dataclass, field
from typing import List, Optional
from .tiles import str_to_idx, is_red
from .params import Params as _Params

_DEFAULT_PARAMS = _Params.best()     # レビューなどAIプレイヤー経由でないときも、調整済みの設定で評価する


@dataclass
class Meld:
    kind: str            # chi / pon / daiminkan / ankan / kakan
    tiles: List[str]     # 全構成牌(mjai文字列)
    target: Optional[int] = None
    called: Optional[str] = None

    @property
    def open(self):
        return self.kind != "ankan"


@dataclass
class Discard:
    tile: str
    tsumogiri: bool
    riichi: bool = False
    called: bool = False


class StateTracker:
    def __init__(self, me: int):
        self.me = me
        self.params = _DEFAULT_PARAMS
        self.reset_round(0, "E", 1, 0, 0, [25000] * 4, [])

    def reset_round(self, oya, bakaze, kyoku, honba, kyotaku, scores, dora_markers):
        self.oya, self.bakaze, self.kyoku = oya, bakaze, kyoku
        self.honba, self.kyotaku = honba, kyotaku
        self.scores = list(scores)
        self.dora_markers = list(dora_markers)
        self.hand: List[str] = []
        self.melds = [[] for _ in range(4)]
        self.discards = [[] for _ in range(4)]
        self.riichi = [False] * 4           # 宣言済(受理待ち含む)
        self.riichi_accepted = [False] * 4
        self.riichi_discard_idx = [None] * 4
        self.ippatsu = [False] * 4
        self.draws = 0
        self.kans = 0
        self.last_discard = None            # (actor, tile)
        self.last_draw: Optional[str] = None
        self.rinshan_next = False
        self.last_meld_turn = [None] * 4     # 各家が最後に副露した時点の自分の河の枚数
        self._dcache = {}
        self.pending_riichi = None
        self.seen_after_riichi = [set() for _ in range(4)]  # 立直後に他家が切った牌(現物扱い)

    # ---- 派生情報 ----
    @property
    def seat_wind(self):
        return "ESWN"[(self.me - self.oya) % 4]

    def seat_wind_of(self, p):
        return "ESWN"[(p - self.oya) % 4]

    @property
    def tiles_left(self):
        return 70 - self.draws - self.kans

    def hand_counts(self):
        c = [0] * 34
        for t in self.hand:
            c[str_to_idx(t)] += 1
        return c

    def visible_counts(self):
        """自分から見えている牌(手牌・河・副露・ドラ表示)の34種カウント。"""
        c = self.hand_counts()
        for p in range(4):
            for d in self.discards[p]:
                if not d.called:
                    c[str_to_idx(d.tile)] += 1
            for m in self.melds[p]:
                for t in m.tiles:
                    c[str_to_idx(t)] += 1
        for t in self.dora_markers:
            c[str_to_idx(t)] += 1
        return c

    def dora_indices(self):
        out = []
        for t in self.dora_markers:
            i = str_to_idx(t)
            if i < 27:
                out.append(i // 9 * 9 + (i % 9 + 1) % 9)
            elif i < 31:
                out.append(27 + (i - 27 + 1) % 4)
            else:
                out.append(31 + (i - 31 + 1) % 3)
        return out

    def is_menzen(self, p=None):
        p = self.me if p is None else p
        return all(not m.open for m in self.melds[p])

    def genbutsu(self, p):
        """プレイヤーpに対する現物集合(34idx)。p自身の河 + 立直後他家が通した牌 + 同巡の見逃し近似は無視。"""
        s = {str_to_idx(d.tile) for d in self.discards[p]}
        s |= self.seen_after_riichi[p]
        return s

    def furiten(self, waits) -> bool:
        mine = {str_to_idx(d.tile) for d in self.discards[self.me]}
        return any(w in mine for w in waits)

    # ---- イベント処理 ----
    def update(self, ev: dict):
        t = ev["type"]
        self._dcache.clear()
        if t in ("chi", "pon", "daiminkan", "ankan", "kakan"):
            a_ = ev["actor"]
            self.last_meld_turn[a_] = len(self.discards[a_])
        if t == "start_kyoku":
            self.reset_round(ev["oya"], ev["bakaze"], ev["kyoku"], ev["honba"], ev["kyotaku"],
                             ev["scores"], [ev["dora_marker"]])
            self.hand = [x for x in ev["tehais"][self.me] if x != "?"]
        elif t == "tsumo":
            if self.rinshan_next:
                self.rinshan_next = False      # 嶺上牌は壁(山)から引かないので残り枚数に数えない
            else:
                self.draws += 1
            if ev["actor"] == self.me:
                self.hand.append(ev["pai"])
                self.last_draw = ev["pai"]
        elif t == "dahai":
            a, pai = ev["actor"], ev["pai"]
            if a == self.me:
                self.hand.remove(pai)
            riichi_now = self.pending_riichi == a
            if riichi_now:
                self.riichi_discard_idx[a] = len(self.discards[a])
                self.pending_riichi = None
            self.discards[a].append(Discard(pai, ev.get("tsumogiri", False), riichi_now))
            self.ippatsu[a] = False
            self.last_discard = (a, pai)
            for q in range(4):
                if q != a and self.riichi[q]:
                    self.seen_after_riichi[q].add(str_to_idx(pai))
        elif t in ("chi", "pon", "daiminkan"):
            a, tg = ev["actor"], ev["target"]
            self.discards[tg][-1].called = True
            m = Meld(t, list(ev["consumed"]) + [ev["pai"]], tg, ev["pai"])
            self.melds[a].append(m)
            if a == self.me:
                for x in ev["consumed"]:
                    self.hand.remove(x)
            if t == "daiminkan":
                self.kans += 1
                self.rinshan_next = True
            self.ippatsu = [False] * 4
        elif t == "ankan":
            a = ev["actor"]
            self.melds[a].append(Meld("ankan", list(ev["consumed"])))
            if a == self.me:
                for x in ev["consumed"]:
                    self.hand.remove(x)
            self.kans += 1
            self.rinshan_next = True
            self.ippatsu = [False] * 4
        elif t == "kakan":
            a = ev["actor"]
            for m in self.melds[a]:
                if m.kind == "pon" and str_to_idx(m.tiles[0]) == str_to_idx(ev["pai"]):
                    m.kind = "kakan"
                    m.tiles = m.tiles + [ev["pai"]]
                    break
            if a == self.me:
                self.hand.remove(ev["pai"])
            self.kans += 1
            self.rinshan_next = True
            self.ippatsu = [False] * 4
        elif t == "dora":
            self.dora_markers.append(ev["dora_marker"])
        elif t == "reach":
            self.riichi[ev["actor"]] = True
            self.pending_riichi = ev["actor"]
        elif t == "reach_accepted":
            a = ev["actor"]
            self.riichi_accepted[a] = True
            self.ippatsu[a] = True
            if "scores" in ev:
                self.scores = list(ev["scores"])
            else:
                self.scores[a] -= 1000
            self.kyotaku += 1
        elif t in ("hora", "ryukyoku"):
            if "scores" in ev:
                self.scores = list(ev["scores"])
            elif "deltas" in ev:
                self.scores = [s + d for s, d in zip(self.scores, ev["deltas"])]
