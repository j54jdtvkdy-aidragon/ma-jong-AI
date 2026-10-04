"""打牌レビュー: mjai形式の牌譜から、指定プレイヤーの判断(打牌・立直/ダマ・鳴き)を
AIの評価と比べ、「何が・なぜ悪かったか」を日本語で解説する。"""
from dataclasses import dataclass, field
from typing import List, Optional

from .agent import (Candidate, evaluate_discards, call_options, choose_call, evaluate_pass,
                    evaluate_call, threats, _kuikae, self_kan_options, kan_judgement, KAN_NAME)

CALL_JP = {"pon": "ポン", "chi": "チー", "daiminkan": "大明槓"}
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


def _kan_loss(st, did: bool, ok: bool) -> float:
    """カン判断のミス損失(点数換算の目安)。"""
    if did and not ok:
        return 600.0 if threats(st) else 300.0
    return 200.0


def _review_kan(st, events, i, out) -> bool:
    """自分のツモ番でのカン判断をレビュー。カンを実行していれば True。"""
    opts = self_kan_options(st)
    nxt = events[i + 1] if i + 1 < len(events) else None
    did_ev = nxt if nxt and nxt["type"] in ("ankan", "kakan") and nxt["actor"] == st.me else None
    if not opts and not did_ev:
        return False
    if did_ev:
        o = next((x for x in opts if x["type"] == did_ev["type"] and (
            did_ev["type"] == "ankan" and str_to_idx(x["consumed"][0]) == str_to_idx(did_ev["consumed"][0])
            or did_ev["type"] == "kakan" and str_to_idx(x["pai"]) == str_to_idx(did_ev["pai"]))), None)
        if o is None:
            return True
        ok, why = kan_judgement(st, o)
        if not ok:
            loss = _kan_loss(st, True, ok)
            out.append(Decision("kan", _label(st), _turn_no(st), hand_str(st.hand),
                                f"{KAN_NAME[o['type']]} {jp(o.get('pai') or o['consumed'][0])}", "カンしない",
                                loss, _severity(loss), [why], False))
        return True
    for o in opts:
        ok, why = kan_judgement(st, o)
        if ok:
            loss = _kan_loss(st, False, ok)
            out.append(Decision("kan", _label(st), _turn_no(st), hand_str(st.hand),
                                "カンしない", f"{KAN_NAME[o['type']]} {jp(o.get('pai') or o['consumed'][0])}",
                                loss, _severity(loss), [why], False))
            break
    return False


def _review_discard(st, events, i, out, forb):
    if not forb and _review_kan(st, events, i, out):
        return
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
    allopts = call_options(st, e["actor"], e["pai"])
    if not allopts:
        return
    nxt = events[i + 1] if i + 1 < len(events) else None
    if nxt and nxt["type"] in ("chi", "pon", "daiminkan", "hora") and nxt.get("actor") != me:
        return        # 他家が優先
    kan_opt = next((o for o in allopts if o["type"] == "daiminkan"), None)
    if kan_opt:
        did_kan = bool(nxt and nxt["type"] == "daiminkan" and nxt["actor"] == me)
        ok, why = kan_judgement(st, kan_opt)
        if did_kan != ok:
            loss = _kan_loss(st, did_kan, ok)
            out.append(Decision("kan", _label(st), _turn_no(st), hand_str(st.hand),
                                f"大明槓 {jp(e['pai'])}" if did_kan else "スルー",
                                "スルー" if did_kan else f"大明槓 {jp(e['pai'])}",
                                loss, _severity(loss), [why], False))
        if did_kan:
            return
    opts = [o for o in allopts if o["type"] != "daiminkan"]
    if not opts:
        return
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
                            f"{CALL_JP[nxt['type']]} {jp(e['pai'])}", "スルー", loss, _severity(loss), rs, False))
    elif (not did) and rec is not None:
        o, c = rec
        loss = max(0.0, c.ev - base["ev"])
        if loss < 150:
            return
        rs = [f"{CALL_JP[o['type']]}でシャンテン数 {base['shanten']}→{c.shanten}、和了期待値 約{base['ev']:.0f}点→約{c.ev:.0f}点。"]
        if c.detail["han"] > 0:
            rs.append("役(役牌・タンヤオ等)が確保でき、スピードアップの価値が守備力低下を上回ります。")
        out.append(Decision("call", _label(st), _turn_no(st), hand_str(st.hand),
                            "スルー", f"{CALL_JP[o['type']]} {jp(e['pai'])}", loss, _severity(loss), rs, False))


def format_report(decisions: List[Decision], top: int = 10, show_all: bool = False) -> str:
    n = len(decisions)
    agree = sum(1 for d in decisions if d.agree)
    bad = sorted((d for d in decisions if d.loss >= 150), key=lambda d: -d.loss)
    lines = [f"■ レビュー結果: 判断{n}回 / AIと一致または同等 {agree}回 ({agree / max(n, 1) * 100:.0f}%) / 問題の判断 {len(bad)}回",
             f"  損失合計(AI評価・点数換算): {sum(d.loss for d in bad):.0f}点相当", ""]
    for k, d in enumerate(bad if show_all else bad[:top], 1):
        lines.append(f"[{k}] {d.round_label} {d.turn}巡目 【{d.severity}】 損失≈{d.loss:.0f}点  ({ {'discard': '打牌', 'call': '鳴き', 'kan': 'カン'}[d.kind] })")
        lines.append(f"    手牌: {d.hand}")
        lines.append(f"    あなた: {d.actual}   AI推奨: {d.best}")
        for r in d.reasons:
            lines.append(f"    ・{r}")
        lines.append("")
    if not bad:
        lines.append("目立つミスはありませんでした。")
    return "\n".join(lines)


# ---------------- 対話アプリ向け: 1回の判断を即時に評価する ----------------
def _cand_row(c: Candidate) -> dict:
    return {"tile": c.tile, "label": jp(c.tile) + ("+立直" if c.riichi else ""), "riichi": c.riichi,
            "shanten": c.shanten, "ukeire": c.ukeire,
            "ukeire_tiles": [jpi(i) for i in c.ukeire_tiles[:10]],
            "danger": round(c.danger, 3), "p_win": round(c.p_win, 3), "ev": round(c.ev),
            "mode": c.mode}


def judge_discard(st, cands, tile: str, riichi: bool) -> dict:
    """候補評価(cands)に対する、実際の打牌(tile, riichi)の評価。"""
    best = cands[0]
    a = _find_candidate(cands, tile, riichi) or _find_candidate(cands, tile, False)
    if a is None:
        return {"kind": "discard", "ok": False}
    loss = max(0.0, best.ev - a.ev)
    same = (a.idx == best.idx and a.riichi == best.riichi) or loss < 1e-6
    sev = "最善" if same else _severity(loss)
    reasons = _explain_discard(st, a, best) if (loss >= 150 and not same) else []
    return {"kind": "discard", "ok": True, "chosen": _c(a), "best": _c(best), "same": same, "loss": round(loss),
            "severity": sev, "reasons": reasons, "turn": _turn_no(st), "hand": hand_str(st.hand),
            "candidates": [_cand_row(c) for c in cands[:5]], "chosen_row": _cand_row(a)}


def judge_call(st, actor: int, tile: str, chosen) -> dict:
    """鳴き(chosen=optまたはNone=スルー)の評価。大明槓も扱う。"""
    from .agent import choose_daiminkan
    kan_rec = choose_daiminkan(st, actor, tile)
    base = evaluate_pass(st)
    name = lambda o: "スルー" if o is None else f"{ {'pon': 'ポン', 'chi': 'チー', 'daiminkan': '大明槓'}[o['type']] } {jp(tile)}"
    out = {"kind": "call", "ok": True, "chosen": name(chosen), "turn": _turn_no(st), "hand": hand_str(st.hand),
           "reasons": [], "loss": 0, "severity": "最善", "same": True}
    if chosen is not None and chosen["type"] == "daiminkan" or (chosen is None and kan_rec is not None):
        o = chosen if chosen is not None else kan_rec
        ok, why = kan_judgement(st, o)
        did = chosen is not None
        out["best"] = name(kan_rec) if kan_rec else "スルー"
        if did != ok:
            out.update(same=False, loss=int(_kan_loss(st, did, ok)), severity=_severity(_kan_loss(st, did, ok)), reasons=[why])
        return out
    rec = choose_call(st, actor, tile)
    out["best"] = name(rec[0]) if rec else "スルー"
    if chosen is None and rec is not None:
        o, c = rec
        loss = max(0.0, c.ev - base["ev"])
        if loss >= 150:
            rs = [f"{ {'pon': 'ポン', 'chi': 'チー'}[o['type']] }でシャンテン数 {base['shanten']}→{c.shanten}、"
                  f"和了期待値 約{base['ev']:.0f}点→約{c.ev:.0f}点になります。"]
            if c.detail["han"] > 0:
                rs.append("役が確保でき、スピードアップの価値が守備力の低下を上回ります。")
            out.update(same=False, loss=round(loss), severity=_severity(loss), reasons=rs)
    elif chosen is not None and rec is None:
        c = evaluate_call(st, actor, tile, chosen)
        loss = max(0.0, base["ev"] - c.ev) if c else 0.0
        if loss >= 150:
            rs = [f"鳴かない場合の和了期待値(約{base['ev']:.0f}点)が、鳴いた場合(約{c.ev:.0f}点)を上回ります。"]
            if c.shanten >= base["shanten"]:
                rs.append(f"鳴いてもシャンテン数が進みません({base['shanten']}→{c.shanten})。")
            if st.is_menzen():
                rs.append("門前を崩すと立直・ツモ・裏ドラの打点を失い、守備力も落ちます。")
            out.update(same=False, loss=round(loss), severity=_severity(loss), reasons=rs)
    elif chosen is not None and rec is not None and sorted(chosen["consumed"]) != sorted(rec[0]["consumed"]):
        out["reasons"] = ["鳴くこと自体は妥当です。どの牌で鳴くかでAIは別の選択でした(赤ドラの扱い・チーの形)。"]
    return out


def judge_kan(st, chosen) -> dict:
    """自分のツモ番での暗槓/加槓の評価(chosen=optまたはNone=しない)。"""
    opts = self_kan_options(st)
    rec = next((o for o in opts if kan_judgement(st, o)[0]), None)
    lab = lambda o: "カンしない" if o is None else f"{KAN_NAME[o['type']]} {jp(o.get('pai') or o['consumed'][0])}"
    out = {"kind": "kan", "ok": True, "chosen": lab(chosen), "best": lab(rec), "turn": _turn_no(st),
           "hand": hand_str(st.hand), "reasons": [], "loss": 0, "severity": "最善", "same": True}
    if (chosen is None) != (rec is None):
        o = chosen if chosen is not None else rec
        ok, why = kan_judgement(st, o)
        did = chosen is not None
        loss = _kan_loss(st, did, ok)
        if did != ok:
            out.update(same=False, loss=int(loss), severity=_severity(loss), reasons=[why])
    return out
