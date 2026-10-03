"""雀魂(段位戦)ルールに準拠した四人麻雀の対局シミュレータ。
 - 25000点持ち/30000点返し、東南戦(西入あり)、赤3枚、喰いタンあり、ダブロンあり、トビ終了、オーラスのアガリ止め
 - 未実装(v1): 槓(暗槓/明槓/加槓)、流し満貫、途中流局(九種九牌・四風連打・四家立直・四開槓)、パオ、頭ハネ以外の細則
 出力は mjai 形式のイベント列で、レビュー機能に直接渡せる。"""
import random
from typing import List, Optional

from mahjong.agari import Agari
from mahjong.constants import EAST, SOUTH, WEST, NORTH
from mahjong.hand_calculating.hand import HandCalculator
from mahjong.hand_calculating.hand_config import HandConfig, OptionalRules
from mahjong.meld import Meld as LMeld

from .agent import call_options
from .shanten import shanten
from .state import StateTracker
from .tiles import id_to_str, str_to_idx, is_red

_CALC = HandCalculator()
_OPTS = OptionalRules(has_open_tanyao=True, has_aka_dora=True, has_double_yakuman=True, kiriage=True)
_WINDS = [EAST, SOUTH, WEST, NORTH]


def _counts(ids):
    c = [0] * 34
    for t in ids:
        c[t // 4] += 1
    return c


class Round:
    def __init__(self, game, oya, bakaze, kyoku, honba, kyotaku, rng):
        self.g = game
        self.oya, self.bakaze, self.kyoku, self.honba, self.kyotaku = oya, bakaze, kyoku, honba, kyotaku
        wall = list(range(136))
        rng.shuffle(wall)
        self.dead, self.live = wall[:14], wall[14:]
        self.hands = [sorted(self.live[i * 13:(i + 1) * 13]) for i in range(4)]
        self.live = self.live[52:]
        self.melds = [[] for _ in range(4)]       # [(kind, ids, called_id, target)]
        self.discards = [[] for _ in range(4)]    # ids
        self.riichi = [False] * 4
        self.ippatsu = [False] * 4
        self.first_turn = True
        self.any_call = False

    # --- 手牌ユーティリティ ---
    def closed_counts(self, p):
        return _counts(self.hands[p])

    def waits(self, p):
        """待ち牌(34idx)。役の有無は問わない。"""
        c = self.closed_counts(p)
        mn = len(self.melds[p])
        if shanten(c, mn) != 0:
            return []
        out = []
        for t in range(34):
            if c[t] >= 4:
                continue
            c[t] += 1
            if shanten(c, mn) == -1:
                out.append(t)
            c[t] -= 1
        return out

    def is_furiten(self, p):
        w = self.waits(p)
        mine = {t // 4 for t in self.discards[p]}
        return any(x in mine for x in w)

    def hand_value(self, p, win_tile, tsumo, haitei):
        ids = list(self.hands[p])
        if not tsumo:
            ids = ids + [win_tile]
        lm = []
        for kind, tiles, called, tgt in self.melds[p]:
            ids += tiles
            lm.append(LMeld(meld_type=LMeld.CHI if kind == "chi" else LMeld.PON, tiles=tiles, opened=True))
        dora = [self.dead[4]]
        if self.riichi[p]:
            dora.append(self.dead[9])
        cfg = HandConfig(
            is_tsumo=tsumo, is_riichi=self.riichi[p], is_ippatsu=self.ippatsu[p],
            is_haitei=tsumo and haitei, is_houtei=(not tsumo) and haitei,
            is_tenhou=tsumo and self.first_turn and p == self.oya and not self.any_call,
            is_chiihou=tsumo and self.first_turn and p != self.oya and not self.any_call and not self.discards[p],
            player_wind=_WINDS[(p - self.oya) % 4], round_wind=_WINDS["ESW".index(self.bakaze)] if self.bakaze in "ESW" else NORTH,
            options=_OPTS)
        r = _CALC.estimate_hand_value(ids, win_tile, melds=lm or None, dora_indicators=dora, config=cfg)
        return None if r.error else r


def play_game(players, seed=None, log=None):
    """players: 4つのエージェント。戻り値 dict(scores, ranks, events)。"""
    rng = random.Random(seed)
    ev = log if log is not None else []
    scores = [25000] * 4
    trackers = [StateTracker(i) for i in range(4)]

    def emit(e, to=None):
        ev.append(e)
        for t in trackers:
            t.update(e)

    emit({"type": "start_game", "id": 0})
    oya, honba, kyotaku = 0, 0, 0
    r_idx = 0       # 0..3 東, 4..7 南, 8..11 西
    while True:
        bakaze = "ESW"[r_idx // 4]
        kyoku = r_idx % 4 + 1
        res = _play_round(players, trackers, emit, Round(None, oya, bakaze, kyoku, honba, kyotaku, rng), scores)
        scores = res["scores"]
        kyotaku = res["kyotaku"]
        emit({"type": "end_kyoku"})
        last_in_extent = (r_idx == 7 and max(scores) >= 30000) or r_idx == 11
        if min(scores) < 0:
            break
        if res["renchan"]:
            honba = res["honba"]
            if r_idx >= 7 and oya == max(range(4), key=lambda i: scores[i]) and scores[oya] >= 30000:
                break
            if r_idx == 11:
                break
        else:
            honba = res["honba"]
            oya = (oya + 1) % 4
            r_idx += 1
            if r_idx == 8 and max(scores) >= 30000:
                break
            if r_idx >= 12:
                break
            if r_idx > 8 and max(scores) >= 30000:
                break
    order = sorted(range(4), key=lambda i: (-scores[i], i))
    ranks = [0] * 4
    for pos, i in enumerate(order):
        ranks[i] = pos + 1
    emit({"type": "end_game", "scores": list(scores)})
    return {"scores": scores, "ranks": ranks, "events": ev}


def _play_round(players, trackers, emit, rd: Round, scores):
    scores = list(scores)
    oya = rd.oya
    emit({"type": "start_kyoku", "bakaze": rd.bakaze, "kyoku": rd.kyoku, "honba": rd.honba,
          "kyotaku": rd.kyotaku, "oya": oya, "scores": list(scores),
          "dora_marker": id_to_str(rd.dead[4]),
          "tehais": [[id_to_str(t) for t in h] for h in rd.hands]})
    cur = oya
    drew = True
    forbidden = set()
    while True:
        if drew:
            if not rd.live:
                return _ryukyoku(rd, emit, scores)
            tile = rd.live.pop(0)
            rd.hands[cur].append(tile)
            emit({"type": "tsumo", "actor": cur, "pai": id_to_str(tile)})
            # ツモ和了
            haitei = not rd.live
            r = rd.hand_value(cur, tile, True, haitei)
            if r:
                return _settle_win(rd, emit, scores, [cur], None, {cur: r}, tsumo=True)
        # 打牌選択
        tg = trackers[cur]
        riichi_decl = False
        if rd.riichi[cur]:
            dtile = rd.hands[cur][-1]
        else:
            choice = players[cur].discard(tg, forbidden)
            tstr = choice["tile"]
            dtile = next(t for t in rd.hands[cur] if id_to_str(t) == tstr)
            if choice.get("riichi"):
                c = rd.closed_counts(cur)
                c[dtile // 4] -= 1
                if (not rd.melds[cur] and shanten(c, 0) == 0 and scores[cur] >= 1000 and len(rd.live) >= 4):
                    riichi_decl = True
        forbidden = set()
        tsumogiri = dtile == rd.hands[cur][-1] and drew
        if riichi_decl:
            emit({"type": "reach", "actor": cur})
        rd.hands[cur].remove(dtile)
        rd.hands[cur].sort()
        rd.discards[cur].append(dtile)
        rd.ippatsu[cur] = False
        emit({"type": "dahai", "actor": cur, "pai": id_to_str(dtile), "tsumogiri": tsumogiri})
        rd.first_turn = rd.first_turn and cur != (oya + 3) % 4
        # ロン判定
        winners = {}
        for k in range(1, 4):
            p = (cur + k) % 4
            if rd.riichi[p] or True:
                if rd.is_furiten(p):
                    continue
            r = rd.hand_value(p, dtile, False, not rd.live)
            if r:
                winners[p] = r
        if winners:
            if len(winners) == 3:
                return _ryukyoku(rd, emit, scores, reason="sanchaho")
            order = [p for p in ((cur + k) % 4 for k in range(1, 4)) if p in winners]
            return _settle_win(rd, emit, scores, order, cur, winners, tsumo=False)
        if riichi_decl:
            rd.riichi[cur] = True
            rd.ippatsu[cur] = True
            scores[cur] -= 1000
            rd.kyotaku += 1
            emit({"type": "reach_accepted", "actor": cur, "scores": list(scores)})
        if not rd.live:
            return _ryukyoku(rd, emit, scores)
        # 鳴き
        called = None
        for k in (1, 2, 3):
            p = (cur + k) % 4
            if rd.riichi[p]:
                continue
            opt = players[p].call(trackers[p], cur, id_to_str(dtile))
            if opt and opt["type"] == "pon":
                called = (p, opt)
                break
            if opt and opt["type"] == "chi" and k == 1 and called is None:
                called = (p, opt)
        if called:
            p, opt = called
            ids = []
            hand = list(rd.hands[p])
            for s in opt["consumed"]:
                t = next(x for x in hand if id_to_str(x) == s)
                hand.remove(t)
                ids.append(t)
            rd.hands[p] = hand
            rd.melds[p].append((opt["type"], ids + [dtile], dtile, cur))
            rd.any_call = True
            rd.ippatsu = [False] * 4
            emit({"type": opt["type"], "actor": p, "target": cur, "pai": id_to_str(dtile),
                  "consumed": opt["consumed"]})
            from .agent import _kuikae
            forbidden = _kuikae({**opt, "pair": tuple(sorted(str_to_idx(s) for s in opt["consumed"]))}, dtile // 4)
            cur, drew = p, False
        else:
            cur, drew = (cur + 1) % 4, True


def _settle_win(rd, emit, scores, order, loser, results, tsumo):
    deltas = [0] * 4
    honba, kyo = rd.honba, rd.kyotaku
    first = True
    for w in order:
        c = results[w].cost
        if tsumo:
            if w == rd.oya:
                for p in range(4):
                    if p != w:
                        pay = c["main"] + 100 * honba
                        deltas[p] -= pay
                        deltas[w] += pay
            else:
                for p in range(4):
                    if p != w:
                        pay = (c["main"] if p == rd.oya else c["additional"]) + 100 * honba
                        deltas[p] -= pay
                        deltas[w] += pay
        else:
            pay = c["main"] + (300 * honba if first else 0)
            deltas[loser] -= pay
            deltas[w] += pay
        if first:
            deltas[w] += 1000 * kyo
            first = False
    new_scores = [s + d for s, d in zip(scores, deltas)]
    for w in order:
        emit({"type": "hora", "actor": w, "target": w if tsumo else loser, "deltas": list(deltas),
              "scores": list(new_scores), "han": results[w].han, "fu": results[w].fu,
              "yaku": [str(y) for y in results[w].yaku],
              "ura_markers": [id_to_str(rd.dead[9])] if rd.riichi[w] else []})
    renchan = rd.oya in order
    return {"scores": new_scores, "kyotaku": 0, "renchan": renchan,
            "honba": rd.honba + 1 if renchan else 0}


def _ryukyoku(rd, emit, scores, reason="fanpai"):
    tenpai = []
    if reason == "fanpai":
        for p in range(4):
            c = rd.closed_counts(p)
            if shanten(c, len(rd.melds[p])) == 0:
                tenpai.append(p)
    deltas = [0] * 4
    n = len(tenpai)
    if 0 < n < 4:
        for p in range(4):
            deltas[p] = 3000 // n if p in tenpai else -3000 // (4 - n)
    new_scores = [s + d for s, d in zip(scores, deltas)]
    emit({"type": "ryukyoku", "reason": reason, "deltas": deltas, "scores": new_scores, "tenpai": tenpai})
    return {"scores": new_scores, "kyotaku": rd.kyotaku, "renchan": rd.oya in tenpai or reason != "fanpai",
            "honba": rd.honba + 1}
