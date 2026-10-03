"""聴牌形の打点を、実際の役判定(mahjong ライブラリ)で計算する。
待ち牌ごとに ロン/ツモ・立直あり/なし の点数を求め、残り枚数で重み付けして期待値にする。
ダマで役がない待ち(ロンできない)は、ロンの価値を0として扱う(ツモは門前なら可)。"""
from mahjong.constants import EAST, SOUTH, WEST, NORTH
from mahjong.hand_calculating.hand import HandCalculator
from mahjong.hand_calculating.hand_config import HandConfig, OptionalRules
from mahjong.meld import Meld as LMeld

from .tiles import str_to_idx, is_red

_CALC = HandCalculator()
_OPTS = OptionalRules(has_open_tanyao=True, has_aka_dora=True, has_double_yakuman=True, kiriage=True)
_WINDS = {"E": EAST, "S": SOUTH, "W": WEST, "N": NORTH}
_RED_TYPES = (4, 13, 22)


class _Alloc:
    """34種+赤フラグから136牌IDを重複なく割り当てる。赤5は type*4+0 に固定。"""

    def __init__(self):
        self.next = {}

    def get(self, tile_str):
        k = str_to_idx(tile_str)
        if is_red(tile_str):
            return k * 4
        j = self.next.get(k, 1 if k in _RED_TYPES else 0)
        self.next[k] = j + 1
        return k * 4 + j

    def get_type(self, k):
        j = self.next.get(k, 1 if k in _RED_TYPES else 0)
        self.next[k] = j + 1
        return k * 4 + min(j, 3)


def _hand_ids(st, hand_strs):
    al = _Alloc()
    meld_objs, ids = [], []
    for m in st.melds[st.me]:
        tid = [al.get(t) for t in m.tiles]
        ids += tid
        mt = LMeld.CHI if m.kind == "chi" else LMeld.PON if m.kind == "pon" else LMeld.KAN
        meld_objs.append(LMeld(meld_type=mt, tiles=tid, opened=m.kind != "ankan"))
    # 赤を先に割り当ててから通常牌(同種の赤と通常牌のIDが衝突しないように)
    for t in sorted(hand_strs, key=lambda x: not is_red(x)):
        ids.append(al.get(t))
    return al, ids, meld_objs


def _dora_ids(st):
    al = _Alloc()
    return [al.get(t) for t in st.dora_markers]


def wait_values(st, hand13_strs, waits, remaining, riichi: bool, tsumo_share: float):
    """hand13_strs: 打牌後の手牌(閉じた部分)の牌文字列。waits: 待ち牌(34idx)。remaining: {idx: 残り枚数}。
    戻り値: (有効枚数u_eff, 1回の和了あたりの平均点)。和了できない待ち(役なし)は枚数に数えない。"""
    al, ids, melds = _hand_ids(st, hand13_strs)
    dora = _dora_ids(st)
    pw = _WINDS[st.seat_wind]
    rw = _WINDS[st.bakaze]
    menzen = st.is_menzen()
    u_eff = 0.0
    total = 0.0
    for w in waits:
        rem = remaining.get(w, 0)
        if rem <= 0:
            continue
        win_id = al.get_type(w)
        tiles = ids + [win_id]
        vals = {}
        for tsumo in (False, True):
            cfg = HandConfig(is_tsumo=tsumo, is_riichi=riichi and menzen, player_wind=pw, round_wind=rw, options=_OPTS)
            r = _CALC.estimate_hand_value(tiles, win_id, melds=melds or None, dora_indicators=dora, config=cfg)
            vals[tsumo] = 0 if r.error else (r.cost["total"] if tsumo else r.cost["main"])
        share_ron, share_ts = 1.0 - tsumo_share, tsumo_share
        weight = share_ron * (vals[False] > 0) + share_ts * (vals[True] > 0)
        u_eff += rem * weight
        total += rem * (share_ron * vals[False] + share_ts * vals[True])
    if u_eff <= 0:
        return 0.0, 0.0
    return u_eff, total / u_eff
