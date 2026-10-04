"""ブラウザ内(Pyodide)で動く対局セッション。サーバー不要・オフライン対応版。
app/session.py のスレッド版と同じ出力(snapshot)を返すが、人間の入力待ちは asyncio で行う。
JS からは new_game / state / act / close を呼ぶ。"""
import asyncio
import json
import random
import time
import uuid

from majai.agent import call_options, evaluate_discards, self_kan_options, KAN_NAME
from majai.engine import play_game_async
from majai.review import judge_call, judge_discard, judge_kan, review_events, jp
from majai.tiles import str_to_idx
from app.session import OPPONENTS, Session, _event_line

SESSIONS = {}


class AsyncHuman:
    name = "human"

    def __init__(self, sess):
        self.sess = sess
        self.st = None

    def bind(self, tracker):
        self.st = tracker
        self.sess.tracker = tracker

    async def _ask(self, prompt):
        s = self.sess
        s.pending_id += 1
        prompt["id"] = s.pending_id
        s.pending = prompt
        s.future = asyncio.get_running_loop().create_future()
        try:
            return await s.future
        finally:
            s.pending = None

    def _push(self, fb):
        st = self.st
        wind = {"E": "東", "S": "南", "W": "西"}[st.bakaze]
        fb["round"] = f"{wind}{st.kyoku}局{st.honba}本場"
        fb["n"] = len(self.sess.feedback) + 1
        self.sess.feedback.append(fb)

    async def discard(self, st, forbidden=()):
        cands = evaluate_discards(st, forbidden)
        ev = self.sess.events
        drawn = ev[-1]["pai"] if ev and ev[-1]["type"] == "tsumo" and ev[-1]["actor"] == 0 else None
        prompt = {"kind": "discard",
                  "forbidden": sorted({t for t in st.hand if str_to_idx(t) in forbidden}), "drawn": drawn,
                  "riichi_tiles": sorted({c.tile for c in cands if c.riichi}),
                  "hint": [{"tile": c.tile, "riichi": c.riichi} for c in cands[:3]]}
        while True:
            act = await self._ask(prompt)
            tile = act.get("tile")
            if tile in st.hand and str_to_idx(tile) not in forbidden:
                break
        riichi = bool(act.get("riichi")) and any(c.riichi and c.tile == tile for c in cands)
        self._push(judge_discard(st, cands, tile, riichi))
        return {"tile": tile, "riichi": riichi}

    async def call(self, st, actor, tile):
        if st.riichi[st.me]:
            return None
        opts = call_options(st, actor, tile)
        if not opts:
            return None
        shown = [{"type": o["type"], "consumed": o["consumed"],
                  "label": {"pon": "ポン", "chi": "チー", "daiminkan": "大明槓"}[o["type"]] + " "
                  + " ".join(jp(t) for t in sorted(o["consumed"], key=str_to_idx))} for o in opts]
        act = await self._ask({"kind": "call", "tile": tile, "from": actor, "options": shown})
        idx = act.get("choice")
        chosen = opts[idx] if isinstance(idx, int) and 0 <= idx < len(opts) else None
        self._push(judge_call(st, actor, tile, chosen))
        return chosen

    async def kan(self, st):
        opts = self_kan_options(st)
        if not opts:
            return None
        shown = [{"type": o["type"], "label": KAN_NAME[o["type"]] + " " + jp(o.get("pai") or o["consumed"][0])}
                 for o in opts]
        act = await self._ask({"kind": "kan", "options": shown})
        idx = act.get("choice")
        chosen = opts[idx] if isinstance(idx, int) and 0 <= idx < len(opts) else None
        self._push(judge_kan(st, chosen))
        return chosen


class LocalSession:
    snapshot = Session.snapshot          # 出力形式をサーバー版と共有
    _table = staticmethod(Session._table)

    def __init__(self, opponent="medium", east_only=True, seed=None):
        self.id = uuid.uuid4().hex[:12]
        self.pending = None
        self.pending_id = 0
        self.future = None
        self.tracker = None
        self.events = []
        self.feedback = []
        self.result = None
        self.error = None
        self.closed = False
        self.opponent = opponent if opponent in OPPONENTS else "medium"
        self.east_only = east_only
        self.seed = seed if seed is not None else random.randrange(1 << 30)
        self.created = time.time()
        self.cv = _NullCV()
        self.task = asyncio.ensure_future(self._run())

    async def _run(self):
        try:
            players = [AsyncHuman(self)] + [OPPONENTS[self.opponent][1]() for _ in range(3)]
            r = await play_game_async(players, seed=self.seed, log=self.events, east_only=self.east_only)
            decisions = review_events(self.events, 0)
            bad = [d for d in decisions if d.loss >= 150]
            self.result = {
                "scores": r["scores"], "ranks": r["ranks"], "decisions": len(decisions),
                "mistakes": [{"round": d.round_label, "turn": d.turn, "kind": d.kind, "severity": d.severity,
                              "loss": round(d.loss), "actual": d.actual, "best": d.best,
                              "hand": d.hand, "reasons": d.reasons}
                             for d in sorted(bad, key=lambda d: -d.loss)[:12]],
                "total_loss": round(sum(d.loss for d in bad)),
            }
        except asyncio.CancelledError:
            pass
        except Exception:
            import traceback
            self.error = traceback.format_exc()

    def close(self):
        self.closed = True
        if self.task and not self.task.done():
            self.task.cancel()

    def act(self, pid, data):
        if self.pending is None or self.pending["id"] != pid or self.future is None or self.future.done():
            return False
        self.future.set_result(data)
        self.pending = None          # 入力を受けたら、すぐに待ち状態を解除する
        return True


class _NullCV:
    """Session.snapshot が使う `with s.cv:` を、単一スレッド用に空実装にする。"""
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def new_game(opponent="medium", east_only=True):
    for sid in [k for k, v in SESSIONS.items() if time.time() - v.created > 4 * 3600]:
        SESSIONS.pop(sid).close()
    s = LocalSession(opponent, bool(east_only))
    SESSIONS[s.id] = s
    return s.id


async def state(sid, ev=0, fb=0):
    s = SESSIONS.get(sid)
    if s is None:
        return json.dumps({"error": "no such session"})
    await asyncio.sleep(0)           # まずエンジンのタスクに順番を譲る
    end = time.time() + 1.5
    while time.time() < end:
        if s.pending is not None or s.result is not None or s.error or len(s.events) > ev:
            break
        await asyncio.sleep(0.03)
    return json.dumps(s.snapshot(ev, fb), ensure_ascii=False)


def act(sid, payload_json):
    s = SESSIONS.get(sid)
    if s is None:
        return False
    data = json.loads(payload_json)
    return s.act(data.get("id"), data)


def close(sid):
    s = SESSIONS.pop(sid, None)
    if s:
        s.close()
    return True
