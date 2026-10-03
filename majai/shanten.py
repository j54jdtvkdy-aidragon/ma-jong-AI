"""高速シャンテン数計算 (面子手 + 七対子 + 国士, 副露数対応)。"""
from functools import lru_cache

_TERMINALS = (0, 8, 9, 17, 18, 26, 27, 28, 29, 30, 31, 32, 33)


def _prune(opts):
    out = []
    for a in opts:
        if not any(b != a and b[0] >= a[0] and b[1] >= a[1] and b[2] >= a[2] for b in opts):
            out.append(a)
    return tuple(out)


@lru_cache(maxsize=None)
def _suit_options(counts: tuple, honors: bool):
    c = list(counts)
    n = len(c)
    best, seen = set(), set()

    def dfs(i, m, t, p):
        while i < n and c[i] == 0:
            i += 1
        if i >= n:
            best.add((m, t, p))
            return
        key = (i, tuple(c), m, t, p)
        if key in seen:
            return
        seen.add(key)
        if c[i] >= 3:
            c[i] -= 3; dfs(i, m + 1, t, p); c[i] += 3
        if not honors and i < 7 and c[i + 1] and c[i + 2]:
            c[i] -= 1; c[i + 1] -= 1; c[i + 2] -= 1
            dfs(i, m + 1, t, p)
            c[i] += 1; c[i + 1] += 1; c[i + 2] += 1
        if c[i] >= 2:
            c[i] -= 2
            if not p:
                dfs(i, m, t, 1)
            dfs(i, m, t + 1, p)
            c[i] += 2
        if not honors:
            if i < 8 and c[i + 1]:
                c[i] -= 1; c[i + 1] -= 1; dfs(i, m, t + 1, p); c[i] += 1; c[i + 1] += 1
            if i < 7 and c[i + 2]:
                c[i] -= 1; c[i + 2] -= 1; dfs(i, m, t + 1, p); c[i] += 1; c[i + 2] += 1
        c[i] -= 1; dfs(i, m, t, p); c[i] += 1

    dfs(0, 0, 0, 0)
    return _prune(list(best))


@lru_cache(maxsize=400000)
def _shanten(counts: tuple, melds: int) -> int:
    opts = {(0, 0, 0)}
    for s in range(4):
        sub = counts[s * 9:s * 9 + 9] if s < 3 else counts[27:34]
        so = _suit_options(sub, s == 3)
        new = set()
        for (m, t, p) in opts:
            for (m2, t2, p2) in so:
                if p + p2 <= 1:
                    new.add((min(m + m2, 4), min(t + t2, 4), p + p2))
        opts = set(_prune(list(new)))
    best = 8
    for (m, t, p) in opts:
        M = m + melds
        T = min(t, 4 - M) if M < 4 else 0
        best = min(best, 8 - 2 * M - T - p)
    if melds == 0:
        pairs = sum(1 for x in counts if x >= 2)
        kinds = sum(1 for x in counts if x >= 1)
        best = min(best, 6 - pairs + max(0, 7 - kinds))
        k = sum(1 for i in _TERMINALS if counts[i] >= 1)
        kp = any(counts[i] >= 2 for i in _TERMINALS)
        best = min(best, 13 - k - (1 if kp else 0))
    return best


def shanten_py(counts34, melds: int = 0) -> int:
    """counts34: 閉じた手牌の34種カウント(13-3*melds枚 or +1枚)。0=聴牌, -1=和了形。"""
    return _shanten(tuple(counts34), melds)


def tenpai_waits(counts34, melds: int = 0) -> frozenset:
    """聴牌形(13-3*melds枚)の待ち牌(34idx)。聴牌でなければ空。"""
    c = list(counts34)
    if shanten(c, melds) != 0:
        return frozenset()
    out = set()
    for t in range(34):
        if c[t] >= 4:
            continue
        c[t] += 1
        if shanten(c, melds) == -1:
            out.add(t)
        c[t] -= 1
    return frozenset(out)


# ---- C実装(あれば優先)。無ければ純Pythonにフォールバック ----
import ctypes
import os
import subprocess

_HERE = os.path.dirname(os.path.abspath(__file__))
_lib = None


def _load():
    global _lib
    src, so = os.path.join(_HERE, "_fast.c"), os.path.join(_HERE, "_fast.so")
    try:
        if not os.path.exists(so) or os.path.getmtime(so) < os.path.getmtime(src):
            subprocess.run(["gcc", "-O2", "-shared", "-fPIC", "-o", so, src], check=True,
                           capture_output=True)
        lib = ctypes.CDLL(so)
        lib.shanten_c.argtypes = [ctypes.POINTER(ctypes.c_int), ctypes.c_int]
        lib.shanten_c.restype = ctypes.c_int
        lib.discard_table.argtypes = [ctypes.POINTER(ctypes.c_int), ctypes.c_int, ctypes.POINTER(ctypes.c_int),
                                      ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int),
                                      ctypes.POINTER(ctypes.c_ulonglong)]
        lib.ukeire_c.argtypes = [ctypes.POINTER(ctypes.c_int), ctypes.c_int, ctypes.POINTER(ctypes.c_int),
                                 ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_ulonglong)]
        lib.ukeire_c.restype = ctypes.c_int
        lib.mc_win.argtypes = [ctypes.POINTER(ctypes.c_int), ctypes.c_int, ctypes.POINTER(ctypes.c_int),
                               ctypes.POINTER(ctypes.c_int), ctypes.c_int, ctypes.c_int, ctypes.c_ulonglong,
                               ctypes.c_int, ctypes.c_double, ctypes.c_double, ctypes.POINTER(ctypes.c_double),
                               ctypes.POINTER(ctypes.c_double)]
        _lib = lib
    except Exception:
        _lib = None


_load()
_A34 = ctypes.c_int * 34


def shanten(counts34, melds: int = 0) -> int:
    """0=聴牌, -1=和了形。"""
    if _lib is None:
        return shanten_py(counts34, melds)
    return _lib.shanten_c(_A34(*counts34), melds)


def _mask_to_list(mask):
    return [t for t in range(34) if mask >> t & 1]


def discard_table(counts14, melds, visible):
    """{idx: (shanten, ukeire, [有効牌idx])}。手牌に存在する牌のみ。"""
    if _lib is None:
        out = {}
        c = list(counts14)
        for i in range(34):
            if not c[i]:
                continue
            c[i] -= 1
            s = shanten_py(c, melds)
            u, tl = 0, []
            if s >= 0:
                for t in range(34):
                    r = 4 - visible[t]
                    if r <= 0:
                        continue
                    c[t] += 1
                    if shanten_py(c, melds) < s:
                        u += r
                        tl.append(t)
                    c[t] -= 1
            c[i] += 1
            out[i] = (s, u, tl)
        return out
    s_ = (ctypes.c_int * 34)()
    u_ = (ctypes.c_int * 34)()
    m_ = (ctypes.c_ulonglong * 34)()
    _lib.discard_table(_A34(*counts14), melds, _A34(*visible), s_, u_, m_)
    return {i: (s_[i], u_[i], _mask_to_list(m_[i])) for i in range(34) if s_[i] != 99}


def ukeire13(counts13, melds, visible):
    """(shanten, ukeire, [有効牌idx])"""
    if _lib is None:
        c = list(counts13)
        s = shanten_py(c, melds)
        u, tl = 0, []
        if s >= 0:
            for t in range(34):
                r = 4 - visible[t]
                if r <= 0:
                    continue
                c[t] += 1
                if shanten_py(c, melds) < s:
                    u += r
                    tl.append(t)
                c[t] -= 1
        return s, u, tl
    s = ctypes.c_int()
    m = ctypes.c_ulonglong()
    u = _lib.ukeire_c(_A34(*counts13), melds, _A34(*visible), ctypes.byref(s), ctypes.byref(m))
    return s.value, u, _mask_to_list(m.value)


def mc_win(counts13, melds, unseen, furi, draws, rollouts, seed, fixed, ron_rate, hazard=0.0):
    """モンテカルロで (ツモ和了確率, ロン和了確率) を推定。C実装が無ければ None。"""
    if _lib is None:
        return None
    pt, pr = ctypes.c_double(), ctypes.c_double()
    _lib.mc_win(_A34(*counts13), melds, _A34(*unseen), _A34(*furi), int(draws), int(rollouts),
                ctypes.c_ulonglong(seed & 0xFFFFFFFFFFFFFFFF), int(fixed), float(ron_rate), float(hazard),
                ctypes.byref(pt), ctypes.byref(pr))
    return pt.value, pr.value
