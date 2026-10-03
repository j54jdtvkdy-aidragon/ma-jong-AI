"""打牌AI: 牌効率(シャンテン・受け入れ) × 打点見積り × 放銃リスクを期待値(点数)で比較する。
レビュー機能は同じ評価 (evaluate_discards) を使い、実際の選択とAI推奨の差を説明する。"""
from dataclasses import dataclass, field
from math import comb
from typing import List, Optional

from .shanten import shanten, tenpai_waits, discard_table, ukeire13, mc_win
from .state import StateTracker
from .tiles import str_to_idx, idx_to_str, is_red, is_terminal_or_honor, suit_num

# ---------------- 打点見積り ----------------
_POINTS = {0: 0, 1: 1300, 2: 2600, 3: 5200, 4: 8000, 5: 8000, 6: 12000, 7: 12000,
           8: 16000, 9: 16000, 10: 16000, 11: 24000, 12: 24000, 13: 32000}


def han_to_points(han: float, dealer: bool) -> float:
    lo = int(han)
    lo = max(0, min(13, lo))
    hi = min(13, lo + 1)
    frac = han - lo
    v = _POINTS[lo] * (1 - frac) + _POINTS[hi] * frac
    return v * (1.5 if dealer else 1.0)


def estimate_han(st: StateTracker, counts, melds, menzen: bool, riichi: bool):
    """手牌+副露から見た役・ドラの概算翻数と『確実な役の有無』。"""
    tot = list(counts)
    reds = sum(1 for t in st.hand if is_red(t))
    for m in melds:
        for t in m.tiles:
            tot[str_to_idx(t)] += 1
            if is_red(t):
                reds += 1
    han = reds
    for d in st.dora_indices():
        han += tot[d]
    yaku = 0.0
    sure = False
    for i, name in ((31, "P"), (32, "F"), (33, "C"), (27 + "ESWN".index(st.bakaze), "b"),
                    (27 + "ESWN".index(st.seat_wind), "s")):
        if tot[i] >= 3:
            yaku += 1
            sure = True
        elif tot[i] == 2:
            yaku += st.params.yakuhai_pair_han
    if st.bakaze == st.seat_wind and tot[27 + "ESWN".index(st.bakaze)] >= 3:
        yaku += 1
    if all(not is_terminal_or_honor(i) for i in range(34) if tot[i]):
        yaku += 1
        sure = True
    for s in range(3):
        suit_tiles = sum(tot[s * 9:s * 9 + 9])
        hon = sum(tot[27:34])
        total = sum(tot)
        if suit_tiles + hon == total and suit_tiles > 0:
            if hon == 0:
                yaku += 6 if menzen else 5
            else:
                yaku += 3 if menzen else 2
            sure = sure or (suit_tiles + hon == total and total >= 9)
    # 対子手・一気通貫などは概算しない。立直の1翻は呼び出し側で加算する
    if menzen:
        yaku += st.params.menzen_yaku_han     # 平和・ツモ・一盃口などの期待
    return han + yaku, sure


# ---------------- 危険度 ----------------
_BASE = {1: (0.060, 0.020), 2: (0.090, 0.040), 3: (0.110, 0.045), 4: (0.130, 0.080)}


def tile_danger(st: StateTracker, idx: int, p: int, visible) -> float:
    """プレイヤーpの待ちに idx が当たる確率の概算 (p が聴牌している前提)。"""
    if idx in st.genbutsu(p):
        return 0.0
    hand_mine = sum(1 for t in st.hand if str_to_idx(t) == idx)
    others_seen = visible[idx] - hand_mine
    if idx >= 27:
        remain = 4 - others_seen
        yk = idx >= 31 or idx == 27 + "ESWN".index(st.bakaze) or idx == 27 + "ESWN".index(st.seat_wind_of(p))
        return (0.017 if yk else 0.012) * remain
    suit, n = idx // 9, idx % 9 + 1
    gen = st.genbutsu(p)
    lo = (idx - 3) in gen if n >= 4 else None
    hi = (idx + 3) in gen if n <= 6 else None
    cls = min(n, 10 - n)
    cls = 4 if cls >= 4 else cls
    if n <= 3:
        suji = hi
        half = False
    elif n >= 7:
        suji = lo
        half = False
    else:
        suji = lo and hi
        half = (lo or hi) and not suji
    base = _BASE[cls]
    if suji:
        pr = base[1] * (0.6 if cls == 4 else 1.0)
    elif half:
        pr = (base[0] + base[1]) / 2
    else:
        pr = base[0]
    if others_seen >= 3:       # 壁・ノーチャンス気味
        pr *= 0.35
    return pr


def threats(st: StateTracker):
    """[(player, weight)]: 立直者は1.0、3副露以上の仕掛けは0.5。"""
    out = []
    for p in range(4):
        if p == st.me:
            continue
        if st.riichi[p]:
            out.append((p, 1.0))
        elif len(st.melds[p]) >= 3:
            out.append((p, st.params.open_threat3))
        elif len(st.melds[p]) == 2 and sum(
                1 for m in st.melds[p] for t in m.tiles if str_to_idx(t) in st.dora_indices() or is_red(t)) >= 2:
            out.append((p, st.params.open_threat2))
    return out


def deal_in_prob(st, idx, visible, ths) -> float:
    q = 1.0
    for p, w in ths:
        q *= 1 - min(0.99, w * tile_danger(st, idx, p, visible) * st.params.danger_scale)
    return 1 - q


def loss_if_deal_in(st, ths) -> float:
    if not ths:
        return 0.0
    p = max(ths, key=lambda x: x[1])[0]
    base = 9500 if p == st.oya else 6500
    L = (base + 1000) * st.params.loss_scale
    if _late(st) and _my_rank(st) == 1:
        L *= st.params.lead_defense
    return L


def _my_rank(st) -> int:
    return 1 + sum(1 for i, x in enumerate(st.scores) if i != st.me and x > st.scores[st.me])


def _late(st) -> bool:
    return ("ESW".index(st.bakaze) * 4 + st.kyoku - 1) >= 6


# ---------------- 和了確率 ----------------
def binom_tail(n: int, q: float, k: int) -> float:
    if k <= 0:
        return 1.0
    n = int(n)
    if n < k:
        return 0.0
    q = min(max(q, 0.0), 1.0)
    return sum(comb(n, i) * q ** i * (1 - q) ** (n - i) for i in range(k, n + 1))


_TYPICAL_UKEIRE = {1: 13, 2: 19, 3: 24}


def p_win_est(s: int, ukeire: int, unseen: int, tiles_left: int, menzen: bool, can_win: bool, P=None) -> float:
    """s+1段階(各段階は毎巡確率qで進む幾何分布)を、残り自摸回数d以内に完了する確率。"""
    if not can_win:
        return 0.0
    d = max(0, int(tiles_left / 4.0))
    unseen = max(unseen, 1)
    from .params import DEFAULT
    P = P or DEFAULT
    boost = 1.0 if menzen else P.open_boost          # 鳴きで進みやすい
    qs = []
    for j in range(s, -1, -1):               # j: そのステージ開始時のシャンテン
        if j == 0:
            u = ukeire if j == s else 4.5
            qs.append(min(0.9, u / unseen * 2.2))
        else:
            u = ukeire if j == s else _TYPICAL_UKEIRE.get(j, 28)
            qs.append(min(0.9, u / unseen * boost))
    # dp[k]: 現在までにk段階クリアしている確率
    dp = [1.0] + [0.0] * len(qs)
    for _ in range(d):
        nxt = [0.0] * len(dp)
        for k, pr in enumerate(dp):
            if pr == 0.0:
                continue
            if k == len(qs):
                nxt[k] += pr
                continue
            nxt[k] += pr * (1 - qs[k])
            nxt[k + 1] += pr * qs[k]
        dp = nxt
    return dp[-1] * P.pwin_scale


# ---------------- 候補評価 ----------------
@dataclass
class Candidate:
    tile: str
    idx: int
    riichi: bool
    shanten: int
    ukeire: int
    ukeire_tiles: List[int]
    danger: float
    p_win: float
    value: float
    ev: float
    mode: str            # attack / fold
    detail: dict = field(default_factory=dict)


def _ukeire(counts, melds_n, base_s, visible):
    tiles, total = [], 0
    for t in range(34):
        r = 4 - visible[t]
        if r <= 0:
            continue
        counts[t] += 1
        if shanten(counts, melds_n) < base_s:
            tiles.append(t)
            total += r
        counts[t] -= 1
    return total, tiles


def _mc_pwin(st, counts13, melds_n, visible, river_extra, fixed, seed):
    """モンテカルロ和了率 (ツモ+ロン)。C実装が無ければ None。役の有無は呼び出し側で扱う。"""
    P = st.params
    unseen = [max(0, 4 - v) for v in visible]
    furi = [0] * 34
    for d in st.discards[st.me]:
        furi[str_to_idx(d.tile)] = 1
    if river_extra is not None:
        furi[river_extra] = 1
    draws = max(1, int(st.tiles_left / 4))
    r = mc_win(counts13, melds_n, unseen, furi, draws, P.mc_rollouts, seed, 1 if fixed else 0,
               P.mc_ron_riichi if fixed else P.mc_ron_dama, P.mc_hazard)
    if r is None:
        return None
    return min(0.97, (r[0] + r[1]) * P.mc_scale)


def evaluate_discards(st: StateTracker, forbidden=()) -> List[Candidate]:
    """14枚持ちの局面で各打牌候補(通常/立直)を評価し、EV降順に返す。"""
    counts = st.hand_counts()
    visible = st.visible_counts()
    melds_n = len(st.melds[st.me])
    ths = threats(st)
    L = loss_if_deal_in(st, ths)
    menzen = st.is_menzen()
    unseen = 136 - sum(visible)
    dealer = st.me == st.oya
    cands = []
    done = set()
    table = discard_table(counts, melds_n, visible)
    best_s = min(v[0] for v in table.values())
    use_mc = st.params.mc_rollouts > 0
    mc_seed = hash((st.me, st.draws, tuple(counts))) & 0xFFFFFFFF
    mc_cache = {}
    for t in st.hand:
        idx = str_to_idx(t)
        if idx in forbidden:
            continue
        key = (idx, is_red(t))
        # 同種牌は赤でない方を優先して候補に(赤を切る候補は別)
        if key in done:
            continue
        done.add(key)
        s, u, utiles = table[idx]
        pd = deal_in_prob(st, idx, visible, ths) if ths else 0.0
        variants = [False]
        if s == 0 and menzen and st.scores[st.me] >= 1000 and st.tiles_left >= 4 and not st.riichi[st.me]:
            variants.append(True)
        for rch in variants:
            han, sure = estimate_han(st, counts, st.melds[st.me], menzen, False)
            riichi_on = rch or st.riichi[st.me]
            can_win = sure or (menzen and (riichi_on or s > 0))
            if menzen and (riichi_on or s > 0):
                han += 1.0 if not sure else (1.0 if riichi_on or s > 0 else 0.0)
            if s == 0 and rch:
                han += st.params.riichi_extra_han
            value = han_to_points(han, dealer) * st.params.value_scale
            if _late(st) and _my_rank(st) == 4:
                value *= st.params.trail_aggr
            pw = None
            if use_mc and best_s <= st.params.mc_max_shanten:
                # この局面ではモンテカルロで統一する(評価方法が混ざると比較が崩れる)。遠い候補は和了率0扱い。
                if not can_win or s > best_s + 2:
                    pw = 0.0
                else:
                    fixed = rch or st.riichi[st.me]
                    key = (idx, fixed)
                    if key not in mc_cache:
                        c13 = counts[:]
                        c13[idx] -= 1
                        mc_cache[key] = _mc_pwin(st, c13, melds_n, visible, idx, fixed, mc_seed)
                    pw = mc_cache[key]
            if pw is None:
                pw = p_win_est(s, u, unseen, st.tiles_left, menzen, can_win, st.params)
                if rch:
                    pw = min(0.8, pw * st.params.riichi_pw_mult)
            # 攻撃: 今回の危険 + 以降の押しで払う危険
            k_future = 0.0
            if ths:
                q_hit = max(u / max(unseen, 1), 0.02)
                k_future = min(st.tiles_left / 4.0, (s + 1) / q_hit * 0.6) * st.params.future_scale
            avg_future = 0.07 * sum(w for _, w in ths) if ths else 0.0
            attack_dealin = min(0.95, pd + k_future * avg_future * (0.6 if s <= 0 else 1.0))
            attack_ev = pw * value - attack_dealin * L
            # オリ: 以降は安全牌を切る前提。安全牌の残りが無いリスクは0.02/巡で近似
            fold_ev = -pd * L - st.params.fold_cost * (1 if ths else 0) - st.params.fold_risk * L * (1 if ths else 0)
            if rch:
                fold_ev = -9e9    # 立直は降りない
            if ths and sum(w for _, w in ths) > 0:
                mode, ev = ("attack", attack_ev) if attack_ev >= fold_ev else ("fold", fold_ev)
            else:
                mode, ev = "attack", attack_ev
            cands.append(Candidate(t, idx, rch, s, u, utiles, pd, pw, value, ev, mode,
                                   {"han": han, "p_dealin_total": attack_dealin, "loss": L}))
    _apply_tactics(st, cands, ths)
    cands.sort(key=lambda c: (-c.ev, c.shanten, -c.ukeire))
    return cands


def _apply_tactics(st, cands, ths):
    """efficiency+ 由来の戦術(立直バイアス・強制オリ・危険牌を押さない)。既定ではすべて無効。"""
    P = st.params
    if P.riichi_bias:
        for c in cands:
            if c.riichi:
                c.ev += P.riichi_bias
    if not ths or not cands:
        return
    riichi_threat = any(w >= 1.0 for _, w in ths)
    best_s = min(c.shanten for c in cands)
    if riichi_threat and best_s >= P.fold_shanten:
        for c in cands:      # 放銃率が低い順 → シャンテン → 受け入れ
            c.mode = "fold"
            c.ev = -1e6 * c.danger - 100.0 * c.shanten + 0.01 * c.ukeire - (1e5 if c.riichi else 0)
    elif riichi_threat and best_s == 0 and P.push_danger_limit < 1.0:
        for c in cands:
            if c.danger > P.push_danger_limit:
                c.ev -= 1e5


def choose_discard(st: StateTracker, forbidden=()) -> dict:
    cs = evaluate_discards(st, forbidden)
    c = cs[0]
    return {"tile": c.tile, "riichi": c.riichi, "cand": c}


# ---------------- 鳴き判断 ----------------
def call_options(st: StateTracker, actor: int, tile: str):
    """tile を自分が鳴ける選択肢: [{'type','consumed'}]"""
    idx = str_to_idx(tile)
    hand = st.hand
    out = []
    by_idx = {}
    for t in hand:
        by_idx.setdefault(str_to_idx(t), []).append(t)
    if len(by_idx.get(idx, [])) >= 3 and st.kans < 4:
        out.append({"type": "daiminkan", "consumed": sorted(by_idx[idx], key=lambda x: is_red(x))[:3]})
    if len(by_idx.get(idx, [])) >= 2:
        cons = sorted(by_idx[idx], key=lambda x: is_red(x))[:2]   # 赤を残す…ではなく赤を使うかは別候補
        out.append({"type": "pon", "consumed": cons})
        reds = [t for t in by_idx[idx] if is_red(t)]
        if reds and len(by_idx[idx]) >= 2:
            non = [t for t in by_idx[idx] if not is_red(t)]
            if non:
                out.append({"type": "pon", "consumed": [reds[0], non[0]]})
    if (actor - st.me) % 4 == 3 and idx < 27:   # 上家(左)の捨て牌からのみチー
        n = idx % 9
        base = idx - n
        for a, b in ((n - 2, n - 1), (n - 1, n + 1), (n + 1, n + 2)):
            if a < 0 or b > 8:
                continue
            ia, ib = base + a, base + b
            if ia in by_idx and ib in by_idx:
                out.append({"type": "chi", "consumed": [by_idx[ia][0], by_idx[ib][0]], "pair": (ia, ib)})
    return out


def _kuikae(opt, idx):
    """喰い替え禁止牌(鳴いた牌と同種、チーなら筋の反対側)。"""
    forb = {idx}
    if opt["type"] == "chi":
        seq = sorted([idx, *opt["pair"]])
        n = idx % 9
        if idx == seq[0] and n + 3 <= 8:
            forb.add(idx + 3)
        if idx == seq[2] and n - 3 >= 0:
            forb.add(idx - 3)
    return forb


def evaluate_call(st: StateTracker, actor: int, tile: str, opt: dict):
    """鳴いた場合の最良EVを返す (打牌後)。"""
    from .state import Meld
    idx = str_to_idx(tile)
    saved_hand, saved_melds = list(st.hand), list(st.melds[st.me])
    try:
        for x in opt["consumed"]:
            st.hand.remove(x)
        st.melds[st.me].append(Meld(opt["type"], opt["consumed"] + [tile], actor, tile))
        # 鳴いた牌は河に残っているため visible に二重計上しない: called フラグを一時付与
        disc = st.discards[actor][-1]
        was = disc.called
        disc.called = True
        forb = _kuikae(opt, idx)
        cs = evaluate_discards(st, forb)
        disc.called = was
    finally:
        st.hand, st.melds[st.me] = saved_hand, saved_melds
    return cs[0] if cs else None


def evaluate_pass(st: StateTracker) -> dict:
    """鳴かない場合: 現手牌(13枚)の和了期待。"""
    counts = st.hand_counts()
    visible = st.visible_counts()
    melds_n = len(st.melds[st.me])
    s, u, _ = ukeire13(counts, melds_n, visible)
    unseen = 136 - sum(visible)
    menzen = st.is_menzen()
    han, sure = estimate_han(st, counts, st.melds[st.me], menzen, False)
    if menzen:
        han += 1.0
    value = han_to_points(han, st.me == st.oya) * st.params.value_scale
    pw = None
    if st.params.mc_rollouts > 0 and s <= st.params.mc_max_shanten + 1:
        pw = 0.0 if not (sure or menzen) else _mc_pwin(st, counts, melds_n, visible, None, st.riichi[st.me],
                      hash((st.me, st.draws, tuple(counts))) & 0xFFFFFFFF)
    if pw is None:
        pw = p_win_est(s, u, unseen, st.tiles_left - 2, menzen, sure or menzen, st.params)
    return {"shanten": s, "ukeire": u, "p_win": pw, "value": value, "ev": pw * value,
            "can_win": sure or menzen}


def _forced_call(st, actor, tile, opts, base):
    """efficiency+ 流の鳴き: 脅威がない場合の役牌ポン / 役が確定した手の前進。"""
    P = st.params
    if not (P.yakuhai_pon_force or P.sure_yaku_call_force) or threats(st):
        return None
    idx = str_to_idx(tile)
    yakuhai = idx >= 31 or idx in (27 + "ESWN".index(st.bakaze), 27 + "ESWN".index(st.seat_wind))
    if P.yakuhai_pon_force and yakuhai:
        for o in opts:
            if o["type"] == "pon":
                c = evaluate_call(st, actor, tile, o)
                if c is not None:
                    return o, c
    if P.sure_yaku_call_force:
        counts = st.hand_counts()
        simples = all(not is_terminal_or_honor(i) for i in range(34) if counts[i])
        if not st.is_menzen() or simples:
            for o in opts:
                c = evaluate_call(st, actor, tile, o)
                if c is not None and c.shanten < base["shanten"]:
                    _, sure = estimate_han(st, counts, st.melds[st.me], False, False)
                    if sure:
                        return o, c
    return None


def choose_call(st: StateTracker, actor: int, tile: str):
    """鳴く価値があれば (opt, candidate) を、なければ None。"""
    opts = [o for o in call_options(st, actor, tile) if o["type"] != "daiminkan"]
    if not opts or st.riichi[st.me] or st.tiles_left < 4:
        return None
    base = evaluate_pass(st)
    forced = _forced_call(st, actor, tile, opts, base)
    if forced is not None:
        return forced
    best = None
    for o in opts:
        c = evaluate_call(st, actor, tile, o)
        if c is None:
            continue
        if best is None or c.ev > best[1].ev:
            best = (o, c)
    if best and best[1].ev > base["ev"] * st.params.call_mult + st.params.call_add and (
            best[1].shanten < base["shanten"] or (not base["can_win"] and best[1].shanten <= base["shanten"])):
        return best
    return None


# ---------------- カン ----------------
KAN_NAME = {"ankan": "暗槓", "kakan": "加槓", "daiminkan": "大明槓"}


def self_kan_options(st: StateTracker):
    """自分のツモ番(14枚)で可能な暗槓・加槓。立直後は、ツモ牌での暗槓かつ待ちが変わらない場合のみ。"""
    me = st.me
    if st.kans >= 4 or st.tiles_left < 1 or st.last_draw is None or len(st.hand) % 3 != 2:
        return []
    by_idx = {}
    for t in st.hand:
        by_idx.setdefault(str_to_idx(t), []).append(t)
    counts = st.hand_counts()
    mn = len(st.melds[me])
    opts = []
    if st.riichi_accepted[me]:
        d = str_to_idx(st.last_draw)
        if len(by_idx.get(d, [])) == 4:
            before = counts[:]
            before[d] -= 1
            w1 = tenpai_waits(before, mn)
            after = counts[:]
            after[d] -= 4
            w2 = tenpai_waits(after, mn + 1)
            if w1 and w1 == w2:
                opts.append({"type": "ankan", "consumed": list(by_idx[d])})
        return opts
    for i, ts in by_idx.items():
        if len(ts) == 4:
            opts.append({"type": "ankan", "consumed": list(ts)})
    for m in st.melds[me]:
        if m.kind == "pon":
            i = str_to_idx(m.tiles[0])
            if by_idx.get(i):
                opts.append({"type": "kakan", "pai": by_idx[i][0], "consumed": list(m.tiles)})
    return opts


def _best_discard_shanten(counts, mn):
    best = 8
    for i in range(34):
        if counts[i]:
            counts[i] -= 1
            best = min(best, shanten(counts, mn))
            counts[i] += 1
    return best


def _leader_protect(st: StateTracker) -> bool:
    last = st.bakaze in "SW" and st.kyoku == 4
    if not last:
        return False
    others = sorted((s for i, s in enumerate(st.scores) if i != st.me), reverse=True)
    return st.scores[st.me] - others[0] >= 8000


def kan_judgement(st: StateTracker, opt: dict):
    """(カンすべきか, 理由)。暗槓/加槓/大明槓すべてに使う。"""
    me, kind = st.me, opt["type"]
    if not st.params.kan_enable:
        return False, "カンは見送る設定です。"
    counts = st.hand_counts()
    mn = len(st.melds[me])
    ths = threats(st)
    if kind == "daiminkan":
        i = str_to_idx(opt["consumed"][0])
        if st.is_menzen():
            return False, "大明槓は門前を崩し、立直・ツモ・裏ドラの打点と守備力を失うため、門前では見送ります。"
        counts[i] -= 3
        s_after = shanten(counts, mn + 1)
        counts[i] += 3
        s_before = shanten(counts, mn)
    elif kind == "ankan":
        i = str_to_idx(opt["consumed"][0])
        if st.riichi_accepted[me]:
            return True, "立直中で待ちが変わらない暗槓です。ドラ・裏ドラが増え、嶺上牌でツモ和了のチャンスも得られます。"
        s_before = _best_discard_shanten(counts, mn)
        counts[i] -= 4
        s_after = shanten(counts, mn + 1)
    else:
        i = str_to_idx(opt["pai"])
        s_before = _best_discard_shanten(counts, mn)
        counts[i] -= 1
        s_after = shanten(counts, mn)
    if ths:
        who = "立直・仕掛け中の相手がいる状況では、新ドラが相手に乗る危険"
        if kind == "kakan":
            who += "と槍槓で放銃する危険"
        return False, who + "が大きく、カンは見送る方が期待値が高いです。"
    if _leader_protect(st):
        return False, "オーラスでトップ目なので、カンドラで局が荒れるリスクを避けて見送ります。"
    if s_after > s_before:
        return False, f"カンするとシャンテン数が{s_before}→{s_after}に後退します。"
    if s_after > st.params.kan_max_shanten:
        return False, "手がまだ遠い(3シャンテン以上)ので、カンで相手にドラを与えるより手を進めます。"
    return True, ("カンしても手が進み(または維持し)、場に脅威がないので、"
                  "カンドラ・符の増加・嶺上牌ツモの利益が相手にドラを与える損を上回ります。")


def choose_self_kan(st: StateTracker):
    for o in self_kan_options(st):
        if kan_judgement(st, o)[0]:
            return o
    return None


def choose_daiminkan(st: StateTracker, actor: int, tile: str):
    if st.riichi[st.me] or st.tiles_left < 2:
        return None
    for o in call_options(st, actor, tile):
        if o["type"] == "daiminkan" and kan_judgement(st, o)[0]:
            return o
    return None
