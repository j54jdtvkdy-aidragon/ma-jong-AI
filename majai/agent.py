"""打牌AI: 牌効率(シャンテン・受け入れ) × 打点見積り × 放銃リスクを期待値(点数)で比較する。
レビュー機能は同じ評価 (evaluate_discards) を使い、実際の選択とAI推奨の差を説明する。"""
from dataclasses import dataclass, field
from math import comb
from typing import List, Optional

from .shanten import shanten
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
            yaku += 0.35
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
        yaku += 0.4          # 平和・ツモ・一盃口などの期待
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
            out.append((p, 0.5))
        elif len(st.melds[p]) == 2 and sum(
                1 for m in st.melds[p] for t in m.tiles if str_to_idx(t) in st.dora_indices() or is_red(t)) >= 2:
            out.append((p, 0.35))
    return out


def deal_in_prob(st, idx, visible, ths) -> float:
    q = 1.0
    for p, w in ths:
        q *= 1 - w * tile_danger(st, idx, p, visible)
    return 1 - q


def loss_if_deal_in(st, ths) -> float:
    if not ths:
        return 0.0
    p = max(ths, key=lambda x: x[1])[0]
    base = 9500 if p == st.oya else 6500
    return base + 1000


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


def p_win_est(s: int, ukeire: int, unseen: int, tiles_left: int, menzen: bool, can_win: bool) -> float:
    """s+1段階(各段階は毎巡確率qで進む幾何分布)を、残り自摸回数d以内に完了する確率。"""
    if not can_win:
        return 0.0
    d = max(0, int(tiles_left / 4.0))
    unseen = max(unseen, 1)
    boost = 1.0 if menzen else 1.35          # 鳴きで進みやすい
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
    return dp[-1] * 0.85


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
    for t in st.hand:
        idx = str_to_idx(t)
        if idx in forbidden:
            continue
        key = (idx, is_red(t))
        # 同種牌は赤でない方を優先して候補に(赤を切る候補は別)
        if key in done:
            continue
        done.add(key)
        counts[idx] -= 1
        s = shanten(counts, melds_n)
        u, utiles = _ukeire(counts, melds_n, s, visible) if s >= 0 else (0, [])
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
                han += 0.5
            value = han_to_points(han, dealer)
            pw = p_win_est(s, u, unseen, st.tiles_left, menzen, can_win)
            if rch:
                pw = min(0.8, pw * 1.05)
            # 攻撃: 今回の危険 + 以降の押しで払う危険
            k_future = 0.0
            if ths:
                q_hit = max(u / max(unseen, 1), 0.02)
                k_future = min(st.tiles_left / 4.0, (s + 1) / q_hit * 0.6)
            avg_future = 0.07 * sum(w for _, w in ths) if ths else 0.0
            attack_dealin = min(0.95, pd + k_future * avg_future * (0.6 if s <= 0 else 1.0))
            attack_ev = pw * value - attack_dealin * L
            # オリ: 以降は安全牌を切る前提。安全牌の残りが無いリスクは0.02/巡で近似
            fold_ev = -pd * L - 600 * (1 if ths else 0) - 0.02 * L * (1 if ths else 0) * 2
            if rch:
                fold_ev = -9e9    # 立直は降りない
            if ths and sum(w for _, w in ths) > 0:
                mode, ev = ("attack", attack_ev) if attack_ev >= fold_ev else ("fold", fold_ev)
            else:
                mode, ev = "attack", attack_ev
            cands.append(Candidate(t, idx, rch, s, u, utiles, pd, pw, value, ev, mode,
                                   {"han": han, "p_dealin_total": attack_dealin, "loss": L}))
        counts[idx] += 1
    cands.sort(key=lambda c: (-c.ev, c.shanten, -c.ukeire))
    return cands


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
    s = shanten(counts, melds_n)
    u, _ = _ukeire(counts, melds_n, s, visible)
    unseen = 136 - sum(visible)
    menzen = st.is_menzen()
    han, sure = estimate_han(st, counts, st.melds[st.me], menzen, False)
    if menzen:
        han += 1.0
    value = han_to_points(han, st.me == st.oya)
    pw = p_win_est(s, u, unseen, st.tiles_left - 2, menzen, sure or menzen)
    return {"shanten": s, "ukeire": u, "p_win": pw, "value": value, "ev": pw * value,
            "can_win": sure or menzen}


def choose_call(st: StateTracker, actor: int, tile: str):
    """鳴く価値があれば (opt, candidate) を、なければ None。"""
    opts = call_options(st, actor, tile)
    if not opts or st.riichi[st.me] or st.tiles_left < 4:
        return None
    base = evaluate_pass(st)
    best = None
    for o in opts:
        c = evaluate_call(st, actor, tile, o)
        if c is None:
            continue
        if best is None or c.ev > best[1].ev:
            best = (o, c)
    if best and best[1].ev > base["ev"] * 1.15 + 150 and (
            best[1].shanten < base["shanten"] or (not base["can_win"] and best[1].shanten <= base["shanten"])):
        return best
    return None
