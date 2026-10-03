"""打牌レビュー: mjai形式の牌譜から、指定プレイヤーの判断(打牌・立直/ダマ・鳴き)を
AIの評価と比べ、「何が・なぜ悪かったか」を日本語で解説する。"""
from dataclasses import dataclass, field
from typing import List, Optional

from .agent import (Candidate, evaluate_discards, call_options, choose_call, evaluate_pass,
                    evaluate_call, threats, _kuikae)
from .state import StateTracker
from .tiles import str_to_idx, idx_to_str, is_red

SEV = [(1000, "大悪手"), (400, "悪手"), (150, "疑問手")]


def jp(t: str) -> str:
    if t == "?":
        return "?"
    if t[0] in "123456789":
        return t[0] + {"m": "萬", "p": "筒", "s": "索"}[t[1]] + ("(赤)" if is_red(t) else "")
    return {"E": "東", "S": "南", "W": "西", "N": "北", "P": "白", "F": "發", "C": "中"}[t]


def jpi(i: int) -> str:
    return jp(idx_to_str(i))


def hand_str(tiles) -> str:
    return " ".join(jp(t) for t in sorted(tiles, key=lambda x: (str_to_idx(x), is_red(x))))


@dataclass
class Decision:
    kind: str                    # discard / call
    round_label: str
    turn: int
    hand: str
    actual: str
    best: str
    loss: float                  # AI評価上のEV損失(点)
    severity: str
    reasons: List[str]
    agree: bool


def _turn_no(st: StateTracker) -> int:
    return len(st.discards[st.me]) + 1


def _label(st: StateTracker) -> str:
    kn = {"E": "東", "S": "南", "W": "西"}[st.bakaze]
    return f"{kn}{st.kyoku}局{st.honba}本場"


def _severity(loss: float) -> str:
    for th, name in SEV:
        if loss >= th:
            return name
    return "概ね同等"


def _explain_discard(st: StateTracker, a: Candidate, b: Candidate) -> List[str]:
    rs = []
    pct = lambda x: f"{x * 100:.1f}%"
    ths = threats(st)
    if a.shanten > b.shanten:
        rs.append(f"{_c(a)}を切るとシャンテン数が{b.shanten}→{a.shanten}に後退します。"
                  f"{_c(b)}なら{b.shanten}シャンテンを維持できます。")
    elif a.shanten < b.shanten:
        if b.mode == "fold":
            rs.append(f"{_c(a)}は{a.shanten}シャンテン(手を進める打牌)ですが、AIは手を崩してでも{_c(b)}で安全に降りる方を選びました。")
        else:
            rs.append(f"{_c(a)}は{a.shanten}シャンテンまで進みますが、AIは{_c(b)}を選びます"
                      f"({b.shanten}シャンテンだが受け入れ{b.ukeire}枚・打点期待が高い)。")
    if a.shanten == b.shanten and b.ukeire > a.ukeire:
        gain = b.ukeire - a.ukeire
        tiles = " ".join(jpi(i) for i in b.ukeire_tiles[:8])
        rs.append(f"受け入れ枚数が{a.ukeire}枚→{b.ukeire}枚(+{gain}枚)。{_c(b)}なら有効牌は {tiles} など。")
    if a.shanten == b.shanten and a.ukeire > b.ukeire:
        rs.append(f"受け入れだけなら{_c(a)}({a.ukeire}枚)が{_c(b)}({b.ukeire}枚)より広いですが、打点・安全度を含めるとAI評価は逆転します。")
    dv = b.detail["han"] - a.detail["han"]
    if dv >= 0.6:
        why = _tile_value_note(st, a.idx, a.tile)
        rs.append(f"打点見積りが約{dv:.1f}翻低下します。{why}")
    if ths:
        if b.danger + 0.01 < a.danger:
            rs.append(f"立直/仕掛け中の相手に対する放銃率の概算が {pct(a.danger)}(実際)→{pct(b.danger)}(推奨)。"
                      f"{_safety_note(st, a.idx, b.idx)}")
        if b.mode == "fold" and a.mode == "attack":
            rs.append("この手牌と点棒状況では押し切るより、安全牌を切って降りる方が期待値が高いと判断しました。")
        if b.mode == "attack" and a.mode == "fold":
            rs.append("手牌が十分に進んでいるため、降りずに押す方が期待値が高い局面です。")
    if a.riichi and not b.riichi and b.mode != "fold":
        rs.append("立直しない(ダマ/見送り)方が、待ちの枚数・打点・放銃リスクを含めて期待値が高い局面です。")
    if b.riichi and not a.riichi:
        rs.append("聴牌しており立直が有利です。ダマだと役がない/打点が伸びず、立直で和了率・打点を稼ぐ方が期待値が高いです。")
    if not rs:
        rs.append("牌効率・打点・安全度の総合評価でわずかに劣ります。")
    return rs


def _c(c: Candidate) -> str:
    return jp(c.tile) + ("+立直" if c.riichi else "")


def _tile_value_note(st, idx, tile) -> str:
    if is_red(tile):
        return "赤ドラを手放しています。"
    if idx in st.dora_indices():
        return "ドラを手放しています。"
    if idx >= 31 or idx in (27 + "ESWN".index(st.bakaze), 27 + "ESWN".index(st.seat_wind)):
        return "役牌(対子/暗刻候補)を崩しています。"
    return "役(タンヤオ・染め手など)の可能性を下げています。"


def _safety_note(st, idx_a, idx_b) -> str:
    for p in range(4):
        if p != st.me and st.riichi[p]:
            g = st.genbutsu(p)
            if idx_b in g and idx_a not in g:
                return f"{jpi(idx_b)}は{p}番席の現物で安全です。"
    return ""


def _find_candidate(cands, tile, riichi) -> Optional[Candidate]:
    ix, red = str_to_idx(tile), is_red(tile)
    for c in cands:
        if c.idx == ix and is_red(c.tile) == red and c.riichi == riichi:
            return c
    for c in cands:        # 赤/非赤の区別なしで再検索
        if c.idx == ix and c.riichi == riichi:
            return c
    return None


def review_events(events, me: int) -> List[Decision]:
    st = StateTracker(me)
    out: List[Decision] = []
    n = len(events)
    for i, e in enumerate(events):
        t = e["type"]
        # 打牌判断: 自分のツモ後、または鳴いた直後
        if t == "tsumo" and e["actor"] == me or (t in ("chi", "pon") and e["actor"] == me):
            st.update(e)
            forb = ()
            if t in ("chi", "pon"):
                opt = {"type": t, "consumed": e["consumed"],
                       "pair": tuple(sorted(str_to_idx(x) for x in e["consumed"]))}
                forb = _kuikae(opt, str_to_idx(e["pai"]))
            _review_discard(st, events, i, out, forb)
            continue
        # 鳴き判断: 他家の打牌に対して鳴ける局面
        if t == "dahai" and e["actor"] != me:
            st.update(e)
            _review_call(st, events, i, out, e)
            continue
        st.update(e)
    return out


def _review_discard(st, events, i, out, forb):
    if st.riichi_accepted[st.me]:
        return
    j, riichi = i + 1, False
    while j < len(events) and events[j]["type"] in ("reach",) and events[j]["actor"] == st.me:
        riichi = True
        j += 1
    if j >= len(events) or events[j]["type"] != "dahai" or events[j]["actor"] != st.me:
        return
    actual_tile = events[j]["pai"]
    hand_before = list(st.hand)
    cands = evaluate_discards(st, forb)
    if not cands:
        return
    best = cands[0]
    a = _find_candidate(cands, actual_tile, riichi)
    if a is None:
        return
    loss = max(0.0, best.ev - a.ev)
    agree = (a.idx == best.idx and a.riichi == best.riichi) or loss < 1e-6
    sev = _severity(loss)
    reasons = _explain_discard(st, a, best) if loss >= 150 else []
    out.append(Decision("discard", _label(st), _turn_no(st), hand_str(hand_before),
                        _c(a), _c(best), loss, sev, reasons, agree or sev == "概ね同等"))


def _review_call(st, events, i, out, e):
    me = st.me
    if st.riichi[me] or st.tiles_left < 4:
        return
    opts = call_options(st, e["actor"], e["pai"])
    if not opts:
        return
    nxt = events[i + 1] if i + 1 < len(events) else None
    if nxt and nxt["type"] in ("chi", "pon", "daiminkan", "hora") and nxt.get("actor") != me:
        return        # 他家が優先
    did = nxt is not None and nxt["type"] in ("chi", "pon") and nxt["actor"] == me
    rec = choose_call(st, e["actor"], e["pai"])
    base = evaluate_pass(st)
    if did and rec is None:
        a_opt = next((o for o in opts if o["type"] == nxt["type"]
                      and sorted(o["consumed"]) == sorted(nxt["consumed"])), None) or \
            next((o for o in opts if o["type"] == nxt["type"]), None)
        c = evaluate_call(st, e["actor"], e["pai"], a_opt) if a_opt else None
        loss = max(0.0, base["ev"] - c.ev) if c else 0.0
        if loss < 150:
            return
        rs = [f"鳴かない場合の和了期待値(約{base['ev']:.0f}点)が、鳴いた場合(約{c.ev:.0f}点)を上回ります。"]
        if c.shanten >= base["shanten"]:
            rs.append(f"鳴いてもシャンテン数が進みません({base['shanten']}→{c.shanten})。")
        if st.is_menzen():
            rs.append("門前を崩すと立直・ツモ・裏ドラの打点を失い、守備力も落ちます。")
        out.append(Decision("call", _label(st), _turn_no(st), hand_str(st.hand),
                            f"{nxt['type']} {jp(e['pai'])}", "スルー", loss, _severity(loss), rs, False))
    elif (not did) and rec is not None:
        o, c = rec
        loss = max(0.0, c.ev - base["ev"])
        if loss < 150:
            return
        rs = [f"{o['type']}でシャンテン数 {base['shanten']}→{c.shanten}、和了期待値 約{base['ev']:.0f}点→約{c.ev:.0f}点。"]
        if c.detail["han"] > 0:
            rs.append("役(役牌・タンヤオ等)が確保でき、スピードアップの価値が守備力低下を上回ります。")
        out.append(Decision("call", _label(st), _turn_no(st), hand_str(st.hand),
                            "スルー", f"{o['type']} {jp(e['pai'])}", loss, _severity(loss), rs, False))


def format_report(decisions: List[Decision], top: int = 10, show_all: bool = False) -> str:
    n = len(decisions)
    agree = sum(1 for d in decisions if d.agree)
    bad = sorted((d for d in decisions if d.loss >= 150), key=lambda d: -d.loss)
    lines = [f"■ レビュー結果: 判断{n}回 / AIと一致または同等 {agree}回 ({agree / max(n, 1) * 100:.0f}%) / 問題の判断 {len(bad)}回",
             f"  損失合計(AI評価・点数換算): {sum(d.loss for d in bad):.0f}点相当", ""]
    for k, d in enumerate(bad if show_all else bad[:top], 1):
        lines.append(f"[{k}] {d.round_label} {d.turn}巡目 【{d.severity}】 損失≈{d.loss:.0f}点  ({'打牌' if d.kind == 'discard' else '鳴き'})")
        lines.append(f"    手牌: {d.hand}")
        lines.append(f"    あなた: {d.actual}   AI推奨: {d.best}")
        for r in d.reasons:
            lines.append(f"    ・{r}")
        lines.append("")
    if not bad:
        lines.append("目立つミスはありませんでした。")
    return "\n".join(lines)
