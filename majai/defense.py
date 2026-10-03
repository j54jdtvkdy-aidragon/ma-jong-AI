"""守備モデル: 自己対戦データで学習した2つの確率モデル。
  1. p_tenpai(st, p)      : 相手pが聴牌している確率 (立直者は1)。副露数・巡目・副露後の手出し等から。
  2. p_hit(st, p, t, vis) : 相手pが聴牌しているとき、牌tが当たり牌である確率。現物・筋・壁・字牌・ドラ等から。
どちらも自分(視点プレイヤー)から見える公開情報だけで計算する。重みは majai/defense_model.json (scripts/train_defense.py で学習)。"""
import json
import math
import os

from .tiles import str_to_idx, is_red

MODEL_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "defense_model.json")
_MODEL = None
_LOADED = False

TILE_FEATURES = [
    "bias", "honor", "dragon", "own_wind", "round_wind", "other_wind",
    "rem0", "rem1", "rem2",
    "n19", "n28", "n37", "n46", "n5",
    "suji_lo", "suji_hi", "suji_both",
    "kabe_m2", "kabe_m1", "kabe_p1", "kabe_p2",
    "near_own_cnt", "own_adj1",
    "dora", "dora_adj",
    "rd_pm1", "rd_pm2", "rd_same_suit",
    "after_gen_lo", "after_gen_hi",
    "nm", "turn", "late",
    "suit_disc", "honor_disc",
]
TENPAI_FEATURES = [
    "bias", "nm1", "nm2", "nm3", "nm4", "turn", "turn2", "dora_melds", "yakuhai_melds",
    "ted_after_meld", "since_meld", "ted_last5", "late_mid", "honors_disc", "open",
]


def load_model():
    global _MODEL, _LOADED
    if not _LOADED:
        _LOADED = True
        try:
            with open(MODEL_PATH) as f:
                _MODEL = json.load(f)
        except Exception:
            _MODEL = None
    return _MODEL


def reload_model():
    global _LOADED
    _LOADED = False
    return load_model()


def available() -> bool:
    return load_model() is not None


def _sig(z):
    if z >= 0:
        return 1.0 / (1.0 + math.exp(-z))
    e = math.exp(z)
    return e / (1.0 + e)


def _wind_idx(w):
    return 27 + "ESWN".index(w)


def _yakuhai_set(st, p):
    return {31, 32, 33, _wind_idx(st.bakaze), _wind_idx(st.seat_wind_of(p))}


def _ctx(st, p):
    key = ("c", p)
    c = st._dcache.get(key)
    if c is not None:
        return c
    ds = st.discards[p]
    own = {str_to_idx(d.tile) for d in ds}
    after = set(st.seen_after_riichi[p]) if st.riichi[p] else set()
    rd = None
    if st.riichi_discard_idx[p] is not None and st.riichi_discard_idx[p] < len(ds):
        rd = str_to_idx(ds[st.riichi_discard_idx[p]].tile)
    dora = set(st.dora_indices())
    suit_cnt = [0, 0, 0]
    hon = 0
    for d in ds:
        i = str_to_idx(d.tile)
        if i < 27:
            suit_cnt[i // 9] += 1
        else:
            hon += 1
    c = {"own": own, "after": after, "gen": own | after, "riichi": bool(st.riichi[p]),
         "nm": len(st.melds[p]), "turn": len(ds), "rd": rd, "dora": dora,
         "dora_adj": {x for d in dora for x in ((d - 1, d + 1) if d < 27 else ()) if x // 9 == d // 9},
         "suit_cnt": suit_cnt, "hon": hon, "yk": _yakuhai_set(st, p), "seat": st.seat_wind_of(p)}
    st._dcache[key] = c
    return c


def tile_features(st, p, t, vis):
    c = _ctx(st, p)
    f = [0.0] * len(TILE_FEATURES)
    f[0] = 1.0
    gen = c["gen"]
    rem = 4 - vis[t]
    f[6], f[7], f[8] = float(rem <= 0), float(rem == 1), float(rem == 2)
    if t >= 27:
        f[1] = 1.0
        f[2] = float(t >= 31)
        f[3] = float(t == _wind_idx(c["seat"]))
        f[4] = float(t == _wind_idx(st.bakaze))
        f[5] = float(27 <= t < 31 and not (f[3] or f[4]))
    else:
        n = t % 9 + 1
        k = min(n, 10 - n)
        f[8 + k] = 1.0 if k != 5 else 0.0
        f[13] = float(n == 5)
        f[9 + (k - 1)] = 1.0 if k < 5 else 0.0
        lo = (t - 3) in gen if n >= 4 else False
        hi = (t + 3) in gen if n <= 6 else False
        f[14], f[15], f[16] = float(lo), float(hi), float(lo and hi)
        for j, off in enumerate((-2, -1, 1, 2)):
            m = n + off
            f[17 + j] = float(1 <= m <= 9 and vis[t + off] >= 4)
        near = sum(1 for off in (-2, -1, 1, 2) if 1 <= n + off <= 9 and (t + off) in c["own"])
        f[21] = near / 4.0
        f[22] = float((n > 1 and (t - 1) in c["own"]) or (n < 9 and (t + 1) in c["own"]))
        f[23] = float(t in c["dora"])
        f[24] = float(t in c["dora_adj"])
        rd = c["rd"]
        if rd is not None and rd < 27 and rd // 9 == t // 9:
            f[27] = 1.0
            f[25] = float(abs(rd - t) == 1)
            f[26] = float(abs(rd - t) == 2)
        # 立直後に他家が通した牌が周囲にあるか(筋以外)
        if n >= 2 and (t - 1) in c["after"]:
            f[28] = 1.0
        if n <= 8 and (t + 1) in c["after"]:
            f[29] = 1.0
        f[33] = c["suit_cnt"][t // 9] / 8.0
    f[30] = c["nm"] / 4.0
    f[31] = min(c["turn"], 18) / 18.0
    f[32] = float(c["turn"] >= 11)
    f[34] = c["hon"] / 4.0
    return f


def tenpai_features(st, p):
    c = _ctx(st, p)
    ds = st.discards[p]
    f = [0.0] * len(TENPAI_FEATURES)
    f[0] = 1.0
    nm = c["nm"]
    for k in range(1, 5):
        f[k] = float(nm == k)
    turn = min(c["turn"], 18) / 18.0
    f[5], f[6] = turn, turn * turn
    lm = st.last_meld_turn[p]
    dm = ym = 0
    for m in st.melds[p]:
        i = str_to_idx(m.tiles[0])
        for tt in m.tiles:
            if str_to_idx(tt) in c["dora"] or is_red(tt):
                dm += 1
        if i in c["yk"]:
            ym += 1
    f[7], f[8] = min(dm, 3) / 3.0, min(ym, 2) / 2.0
    if lm is not None:
        f[9] = min(sum(1 for d in ds[lm:] if not d.tsumogiri), 6) / 3.0
        f[10] = min(len(ds) - lm, 10) / 6.0
    f[11] = sum(1 for d in ds[-5:] if not d.tsumogiri) / 5.0
    late = 0
    for i, d in enumerate(ds):
        x = str_to_idx(d.tile)
        if i >= 8 and x < 27 and 2 <= x % 9 <= 6:
            late += 1
    f[12] = min(late, 5) / 5.0
    f[13] = min(c["hon"], 4) / 4.0
    f[14] = float(nm > 0)
    return f


def _dot(w, x):
    return sum(a * b for a, b in zip(w, x))


def p_tenpai(st, p):
    if st.riichi[p]:
        return 1.0
    m = load_model()
    if m is None:
        return None
    return _sig(_dot(m["tenpai"]["w"], tenpai_features(st, p)))


def p_hit(st, p, t, vis):
    """pが聴牌しているとき t が当たる確率。現物(フリテン)は0。"""
    c = _ctx(st, p)
    if t in c["own"] or t in c["after"]:
        return 0.0
    m = load_model()
    if m is None:
        return None
    w = m["tile"]["riichi" if c["riichi"] else "other"]["w"]
    return _sig(_dot(w, tile_features(st, p, t, vis)))
