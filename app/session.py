"""対話式の対局セッション。エンジンを別スレッドで動かし、人間の番になるとブラウザの操作を待つ。
自分(席0)が打つたびに、AIの推奨との比較(解説)を feedback に積む。"""
import random
import threading
import time
import uuid

from majai.agent import call_options, evaluate_discards, self_kan_options, KAN_NAME
from majai.engine import play_game
from majai.params import Params
from majai.players import AIPlayer, EfficiencyPlayer, EfficiencyPlusPlayer
from majai.review import (judge_call, judge_discard, judge_kan, review_events, jp, jpi, hand_str)
from majai.tiles import str_to_idx, is_red

SEAT_NAMES = ["あなた", "下家", "対面", "上家"]
OPPONENTS = {
    "strong": ("強い(調整済みAI)", lambda: AIPlayer()),
    "medium": ("ふつう(強い素朴bot)", lambda: EfficiencyPlusPlayer()),
    "weak": ("やさしい(素朴bot)", lambda: EfficiencyPlayer()),
}


class Aborted(Exception):
    pass


def _hand_sorted(tiles):
    return sorted(tiles, key=lambda t: (str_to_idx(t), is_red(t)))


class HumanPlayer:
    name = "human"

    def __init__(self, sess):
        self.sess = sess
        self.st = None

    def bind(self, tracker):
        self.st = tracker
        self.sess.tracker = tracker

    # ---- 入力待ち ----
    def _ask(self, prompt):
        s = self.sess
        with s.cv:
            s.pending_id += 1
            prompt["id"] = s.pending_id
            s.pending = prompt
            s.action = None
            s.cv.notify_all()
            while s.action is None and not s.closed:
                s.cv.wait(timeout=1.0)
            act, s.pending = s.action, None
            s.cv.notify_all()
        if s.closed:
            raise Aborted()
        return act

    def _label(self):
        st = self.st
        return f"{ {'E': '東', 'S': '南', 'W': '西'}[st.bakaze] }{st.kyoku}局{st.honba}本場"

    def _push(self, fb):
        fb["round"] = self._label()
        fb["n"] = len(self.sess.feedback) + 1
        self.sess.feedback.append(fb)

    # ---- エンジンから呼ばれる ----
    def discard(self, st, forbidden=()):
        cands = evaluate_discards(st, forbidden)
        ev = self.sess.events
        drawn = ev[-1]["pai"] if ev and ev[-1]["type"] == "tsumo" and ev[-1]["actor"] == 0 else None
        riichi_tiles = sorted({c.tile for c in cands if c.riichi})
        prompt = {"kind": "discard", "forbidden": sorted({t for t in st.hand if str_to_idx(t) in forbidden}), "drawn": drawn,
                  "riichi_tiles": riichi_tiles, "hint": [{"tile": c.tile, "riichi": c.riichi} for c in cands[:3]]}
        while True:
            act = self._ask(prompt)
            tile = act.get("tile")
            if tile in st.hand and str_to_idx(tile) not in forbidden:
                break
        riichi = bool(act.get("riichi")) and any(c.riichi and c.tile == tile for c in cands)
        fb = judge_discard(st, cands, tile, riichi)
        self._push(fb)
        return {"tile": tile, "riichi": riichi}

    def call(self, st, actor, tile):
        if st.riichi[st.me]:
            return None
        opts = call_options(st, actor, tile)
        if not opts:
            return None
        shown = []
        for o in opts:
            shown.append({"type": o["type"], "consumed": o["consumed"],
                          "label": {"pon": "ポン", "chi": "チー", "daiminkan": "大明槓"}[o["type"]]
                          + " " + " ".join(jp(t) for t in sorted(o["consumed"], key=str_to_idx))})
        prompt = {"kind": "call", "tile": tile, "from": actor, "options": shown}
        act = self._ask(prompt)
        idx = act.get("choice")
        chosen = opts[idx] if isinstance(idx, int) and 0 <= idx < len(opts) else None
        self._push(judge_call(st, actor, tile, chosen))
        return chosen

    def kan(self, st):
        opts = self_kan_options(st)
        if not opts:
            return None
        shown = [{"type": o["type"], "label": KAN_NAME[o["type"]] + " " + jp(o.get("pai") or o["consumed"][0])}
                 for o in opts]
        act = self._ask({"kind": "kan", "options": shown})
        idx = act.get("choice")
        chosen = opts[idx] if isinstance(idx, int) and 0 <= idx < len(opts) else None
        self._push(judge_kan(st, chosen))
        return chosen


def _event_line(e):
    t = e["type"]
    who = SEAT_NAMES[e["actor"]] if "actor" in e else ""
    if t == "start_kyoku":
        wind = {"E": "東", "S": "南", "W": "西"}[e["bakaze"]]
        return f"―― {wind}{e['kyoku']}局 {e['honba']}本場 開始 ――"
    if t == "dahai":
        return f"{who} 打 {jp(e['pai'])}"
    if t in ("pon", "chi", "daiminkan"):
        return f"{who} {({'pon': 'ポン', 'chi': 'チー', 'daiminkan': '大明槓'})[t]} {jp(e['pai'])}"
    if t == "ankan":
        return f"{who} 暗槓 {jp(e['consumed'][0])}"
    if t == "kakan":
        return f"{who} 加槓 {jp(e['pai'])}"
    if t == "reach":
        return f"{who} 立直！"
    if t == "dora":
        return f"新ドラ表示牌 {jp(e['dora_marker'])}"
    if t == "hora":
        kind = "ツモ" if e["actor"] == e["target"] else f"ロン({SEAT_NAMES[e['target']]}から)"
        d = e["deltas"][e["actor"]]
        return f"{who} {kind}和了！ {e['han']}翻{e['fu']}符 {', '.join(e['yaku'])} (+{d}点)"
    if t == "ryukyoku":
        return {"fanpai": "流局(荒牌平局)", "suukaikan": "流局(四開槓)", "sanchaho": "流局(三家和)"}.get(e.get("reason"), "流局")
    if t == "end_game":
        return "―― 終局 ――"
    return None


class Session:
    def __init__(self, opponent="medium", east_only=True, seed=None):
        self.id = uuid.uuid4().hex[:12]
        self.cv = threading.Condition()
        self.pending = None
        self.pending_id = 0
        self.action = None
        self.closed = False
        self.tracker = None
        self.events = []
        self.feedback = []
        self.result = None
        self.error = None
        self.opponent = opponent if opponent in OPPONENTS else "medium"
        self.east_only = east_only
        self.seed = seed if seed is not None else random.randrange(1 << 30)
        self.created = time.time()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self):
        try:
            players = [HumanPlayer(self)] + [OPPONENTS[self.opponent][1]() for _ in range(3)]
            r = play_game(players, seed=self.seed, log=self.events, east_only=self.east_only)
            decisions = review_events(self.events, 0)
            bad = [d for d in decisions if d.loss >= 150]
            self.result = {
                "scores": r["scores"], "ranks": r["ranks"],
                "decisions": len(decisions),
                "mistakes": [{"round": d.round_label, "turn": d.turn, "kind": d.kind, "severity": d.severity,
                              "loss": round(d.loss), "actual": d.actual, "best": d.best,
                              "hand": d.hand, "reasons": d.reasons}
                             for d in sorted(bad, key=lambda d: -d.loss)[:12]],
                "total_loss": round(sum(d.loss for d in bad)),
            }
        except Aborted:
            pass
        except Exception as ex:      # 画面に出す
            import traceback
            self.error = traceback.format_exc()

    def close(self):
        with self.cv:
            self.closed = True
            self.cv.notify_all()

    def act(self, pid, data):
        with self.cv:
            if self.pending is None or self.pending["id"] != pid:
                return False
            self.action = data
            self.cv.notify_all()
        return True

    def snapshot(self, ev_cursor=0, fb_cursor=0):
        with self.cv:
            pending = self.pending
            events = list(self.events)
            fbs = list(self.feedback)
        lines = []
        for e in events[ev_cursor:]:
            ln = _event_line(e)
            if ln:
                lines.append(ln)
        snap = {"events": lines, "ev_cursor": len(events), "feedback": fbs[fb_cursor:], "fb_cursor": len(fbs),
                "finished": self.result is not None, "result": self.result, "error": self.error,
                "pending": None, "opponent": OPPONENTS[self.opponent][0]}
        st = self.tracker
        if pending is not None and st is not None:
            snap["pending"] = pending
            snap["table"] = self._table(st, pending)
        elif st is not None and self.result is not None:
            snap["table"] = self._table(st, None)
        return snap

    @staticmethod
    def _table(st, pending):
        drawn = pending.get("drawn") if pending else None
        hand = list(st.hand)
        if drawn and drawn in hand:
            hand.remove(drawn)
        players = []
        for p in range(4):
            players.append({
                "seat": p, "name": SEAT_NAMES[p], "wind": st.seat_wind_of(p), "score": st.scores[p],
                "riichi": bool(st.riichi[p]), "dealer": p == st.oya,
                "discards": [{"t": d.tile, "tsumogiri": d.tsumogiri, "riichi": d.riichi, "called": d.called}
                             for d in st.discards[p]],
                "melds": [{"kind": m.kind, "tiles": m.tiles} for m in st.melds[p]],
            })
        return {"players": players, "hand": _hand_sorted(hand), "drawn": drawn,
                "dora": list(st.dora_markers), "bakaze": st.bakaze, "kyoku": st.kyoku, "honba": st.honba,
                "kyotaku": st.kyotaku, "tiles_left": st.tiles_left}
