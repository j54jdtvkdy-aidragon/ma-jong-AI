"""牌表現。34種インデックス: 0-8 萬, 9-17 筒, 18-26 索, 27-33 東南西北白發中。
mjai文字列: 1m..9m 1p..9p 1s..9s E S W N P F C, 赤五は 5mr 5pr 5sr, 伏せ牌は ?。
136牌ID: idx*4+k。赤五は 5m=16, 5p=52, 5s=88 (k==0)。
"""
HONORS = ["E", "S", "W", "N", "P", "F", "C"]
RED_IDS = (16, 52, 88)


def str_to_idx(s: str) -> int:
    if s[0] in "123456789":
        return "mps".index(s[1]) * 9 + int(s[0]) - 1
    return 27 + HONORS.index(s)


def idx_to_str(i: int, red: bool = False) -> str:
    if i >= 27:
        return HONORS[i - 27]
    return f"{i % 9 + 1}{'mps'[i // 9]}" + ("r" if red else "")


def is_red(s: str) -> bool:
    return len(s) == 3


def id_to_str(t: int) -> str:
    return idx_to_str(t // 4, t in RED_IDS)


def is_terminal_or_honor(i: int) -> bool:
    return i >= 27 or i % 9 in (0, 8)


def suit_num(i: int):
    """(suit, number1-9) 字牌は (3, None)"""
    return (i // 9, i % 9 + 1) if i < 27 else (3, None)
