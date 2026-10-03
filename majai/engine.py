"""雀魂(段位戦)ルールに準拠した四人麻雀の対局シミュレータ。
 - 25000点持ち/30000点返し、東南戦(西入あり)、赤3枚、喰いタンあり、ダブロンあり、トビ終了、オーラスのアガリ止め
 - 槓: 暗槓/加槓/大明槓、嶺上開花、槍槓(加槓のみ)、カンドラ(暗槓は即時、明槓/加槓は打牌後)、四開槓流局
 - 未実装: 流し満貫、途中流局(九種九牌・四風連打・四家立直)、パオ、頭ハネ以外の細則
 出力は mjai 形式のイベント列で、レビュー機能に直接渡せる。"""
import random
from typing import List, Optional

from mahjong.agari import Agari
from mahjong.constants import EAST, SOUTH, WEST, NORTH
from mahjong.hand_calculating.hand import HandCalculator
from mahjong.hand_calculating.hand_config import HandConfig, OptionalRules
from mahjong.meld import Meld as LMeld

from .agent import call_options, self_kan_options
from .shanten import shanten
from .state import StateTracker
from .tiles import id_to_str, str_to_idx, is_red

KANS = ('ankan', 'kakan', 'daiminkan')
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
        self.dora_n = 1
        self.rin = 0
        self.pending_dora = 0

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

    def kan_count(self, p=None):
        ps = range(4) if p is None else [p]
        return sum(1 for q in ps for m in self.melds[q] if m[0] in KANS)

    def hand_value(self, p, win_tile, tsumo, haitei, rinshan=False, chankan=False):
        ids = list(self.hands[p])
        if not tsumo:
            ids = ids + [win_tile]
        lm = []
        for kind, tiles, called, tgt in self.melds[p]:
            ids += tiles
            mt = LMeld.CHI if kind == "chi" else LMeld.PON if kind == "pon" else LMeld.KAN
            lm.append(LMeld(meld_type=mt, tiles=tiles, opened=kind != "ankan"))
        dora = list(self.dead[4:4 + self.dora_n])
        if self.riichi[p]:
            dora += self.dead[9:9 + self.dora_n]
        cfg = HandConfig(
            is_tsumo=tsumo, is_riichi=self.riichi[p], is_ippatsu=self.ippatsu[p],
            is_rinshan=rinshan, is_chankan=chankan,
            is_haitei=tsumo and haitei, is_houtei=(not tsumo) and haitei,
            is_tenhou=tsumo and self.first_turn and p == self.oya and not self.any_call,
            is_chiihou=tsumo and self.first_turn and p != self.oya and not self.any_call and not self.discards[p],
            player_wind=_WINDS[(p - self.oya) % 4], round_wind=_WINDS["ESW".index(self.bakaze)] if self.bakaze in "ESW" else NORTH,
            options=_OPTS)
        r = _CALC.estimate_hand_value(ids, win_tile, melds=lm or None, dora_indicators=dora, config=cfg)
        return None if r.error else r


HOOK = None     # 学習データ収集用: 打牌のたびに HOOK(rd, trackers) が呼ばれる


def play_game(players, seed=None, log=None, hook=None):
    """players: 4つのエージェント。戻り値 dict(scores, ranks, events)。"""
    global HOOK
    HOOK = hook
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


def _reveal_dora(rd, emit):
    rd.dora_n += 1
    emit({"type": "dora", "dora_marker": id_to_str(rd.dead[3 + rd.dora_n])})


def _after_kan(rd):
    """カンの後始末: 王牌を補充(壁を1枚減らす)。"""
    if rd.live:
        rd.dead.append(rd.live.pop())


def _play_round(players, trackers, emit, rd: Round, scores):
    scores = list(scores)
    oya = rd.oya
    emit({"type": "start_kyoku", "bakaze": rd.bakaze, "kyoku": rd.kyoku, "honba": rd.honba,
          "kyotaku": rd.kyotaku, "oya": oya, "scores": list(scores),
          "dora_marker": id_to_str(rd.dead[4]),
          "tehais": [[id_to_str(t) for t in h] for h in rd.hands]})
    cur = oya
    drew = True
    rinshan = False
    forbidden = set()
    while True:
        if drew:
            if rinshan:
                tile = rd.dead[rd.rin]
                rd.rin += 1
            else:
                if not rd.live:
                    return _ryukyoku(rd, emit, scores)
                tile = rd.live.pop(0)
            rd.hands[cur].append(tile)
            emit({"type": "tsumo", "actor": cur, "pai": id_to_str(tile)})
            haitei = not rd.live and not rinshan
            r = rd.hand_value(cur, tile, True, haitei, rinshan=rinshan)
            if r:
                return _settle_win(rd, emit, scores, [cur], None, {cur: r}, tsumo=True)
        # カン判断 (暗槓/加槓)
        tg = trackers[cur]
        if drew and rd.live and rd.kan_count() < 4:
            opts = self_kan_options(tg)
            kopt = players[cur].kan(tg) if opts else None
            if kopt is not None:
                res = _do_self_kan(players, trackers, emit, rd, scores, cur, kopt, opts)
                if res is not None:
                    return res
                rinshan = True
                forbidden = set()
                continue
        rinshan = False
        # 打牌選択
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
        if HOOK is not None:
            HOOK(rd, trackers)
        while rd.pending_dora:           # 明槓・加槓のカンドラは打牌後にめくる
            rd.pending_dora -= 1
            _reveal_dora(rd, emit)
        rd.first_turn = rd.first_turn and cur != (oya + 3) % 4
        # ロン判定
        winners = {}
        for k in range(1, 4):
            p = (cur + k) % 4
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
        if rd.kan_count() == 4 and max(rd.kan_count(p) for p in range(4)) < 4:
            return _ryukyoku(rd, emit, scores, reason="suukaikan")
        if not rd.live:
            return _ryukyoku(rd, emit, scores)
        # 鳴き (ロン > ポン/大明槓 > チー)
        called = None
        for k in (1, 2, 3):
            p = (cur + k) % 4
            if rd.riichi[p]:
                continue
            opt = players[p].call(trackers[p], cur, id_to_str(dtile))
            if opt and opt["type"] in ("pon", "daiminkan"):
                called = (p, opt)
                break
            if opt and opt["type"] == "chi" and k == 1 and called is None:
                called = (p, opt)
        if called:
            p, opt = called
            ids = []
            hand = list(rd.hands[p])
            for s_ in opt["consumed"]:
                t = next(x for x in hand if id_to_str(x) == s_)
                hand.remove(t)
                ids.append(t)
            rd.hands[p] = hand
            rd.melds[p].append((opt["type"], ids + [dtile], dtile, cur))
            rd.any_call = True
            rd.ippatsu = [False] * 4
            emit({"type": opt["type"], "actor": p, "target": cur, "pai": id_to_str(dtile),
                  "consumed": opt["consumed"]})
            if opt["type"] == "daiminkan":
                rd.pending_dora += 1
                _after_kan(rd)
                cur, drew, rinshan = p, True, True
                continue
            from .agent import _kuikae
            forbidden = _kuikae({**opt, "pair": tuple(sorted(str_to_idx(s_) for s_ in opt["consumed"]))}, dtile // 4)
            cur, drew = p, False
        else:
            cur, drew = (cur + 1) % 4, True


def _do_self_kan(players, trackers, emit, rd, scores, cur, kopt, opts):
    """暗槓/加槓を実行。槍槓で終局した場合は結果dictを、続行なら None を返す。"""
    if not any(o["type"] == kopt["type"] and o["consumed"] == kopt["consumed"] for o in opts):
        raise ValueError(f"illegal kan {kopt}")
    rd.any_call = True
    rd.ippatsu = [False] * 4
    if kopt["type"] == "ankan":
        ids = []
        hand = list(rd.hands[cur])
        for s_ in kopt["consumed"]:
            t = next(x for x in hand if id_to_str(x) == s_)
            hand.remove(t)
            ids.append(t)
        rd.hands[cur] = hand
        rd.melds[cur].append(("ankan", ids, None, None))
        emit({"type": "ankan", "actor": cur, "consumed": kopt["consumed"]})
        _reveal_dora(rd, emit)
        _after_kan(rd)
        return None
    # 加槓
    t = next(x for x in rd.hands[cur] if id_to_str(x) == kopt["pai"])
    rd.hands[cur].remove(t)
    for k, m in enumerate(rd.melds[cur]):
        if m[0] == "pon" and m[1][0] // 4 == t // 4:
            rd.melds[cur][k] = ("kakan", m[1] + [t], m[2], m[3])
            break
    emit({"type": "kakan", "actor": cur, "pai": kopt["pai"], "consumed": kopt["consumed"]})
    winners = {}
    for k in range(1, 4):
        p = (cur + k) % 4
        if rd.is_furiten(p):
            continue
        r = rd.hand_value(p, t, False, False, chankan=True)
        if r:
            winners[p] = r
    if winners:
        order = [p for p in ((cur + k) % 4 for k in range(1, 4)) if p in winners]
        return _settle_win(rd, emit, scores, order, cur, winners, tsumo=False)
    rd.pending_dora += 1
    _after_kan(rd)
    return None


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
              "ura_markers": [id_to_str(rd.dead[9 + k]) for k in range(rd.dora_n)] if rd.riichi[w] else []})
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
