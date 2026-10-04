import json
import threading
import time
import urllib.request

import pytest

from app.server import make_server


@pytest.fixture(scope="module")
def base():
    srv = make_server("127.0.0.1", 0)
    port = srv.server_address[1]
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    yield f"http://127.0.0.1:{port}"
    srv.shutdown()


def call(base, path, body=None):
    req = urllib.request.Request(base + path, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def test_index_served(base):
    with urllib.request.urlopen(base + "/", timeout=10) as r:
        html = r.read().decode()
    assert "打牌トレーニング" in html


def test_full_game_with_scripted_human(base):
    sid = call(base, "/api/new", {"opponent": "weak", "east_only": True})["sid"]
    ev, fb = 0, 0
    feedback, kinds = [], set()
    end = time.time() + 240
    result = None
    while time.time() < end:
        s = call(base, f"/api/state?sid={sid}&ev={ev}&fb={fb}")
        assert not s.get("error"), s.get("error")
        ev, fb = s["ev_cursor"], s["fb_cursor"]
        feedback += s["feedback"]
        p = s["pending"]
        if s["finished"]:
            result = s["result"]
            break
        if p:
            kinds.add(p["kind"])
            if p["kind"] == "discard":
                # 立直できるなら立直、できなければ先頭の切れる牌
                tbl = s["table"]
                tiles = tbl["hand"] + ([tbl["drawn"]] if tbl["drawn"] else [])
                tile = p["riichi_tiles"][0] if p["riichi_tiles"] else tiles[-1]
                call(base, "/api/action", {"sid": sid, "id": p["id"], "tile": tile, "riichi": bool(p["riichi_tiles"])})
            else:
                call(base, "/api/action", {"sid": sid, "id": p["id"], "choice": 0 if p["kind"] == "call" and len(feedback) % 2 else None})
    assert result is not None, "game did not finish"
    assert sorted(result["ranks"]) == [1, 2, 3, 4]
    assert feedback and all("severity" in f for f in feedback)
    assert "discard" in kinds
    # 立直後はツモ切りなので、プロンプトが出ない(=自動進行)ことも含め、最後まで進めば十分
    call(base, "/api/close", {"sid": sid})


def test_stale_action_rejected(base):
    sid = call(base, "/api/new", {"opponent": "weak", "east_only": True})["sid"]
    end = time.time() + 30
    while time.time() < end:
        s = call(base, f"/api/state?sid={sid}&ev=0&fb=0")
        if s["pending"]:
            break
    assert s["pending"]
    r = call(base, "/api/action", {"sid": sid, "id": s["pending"]["id"] + 99, "tile": s["table"]["hand"][0]})
    assert r["ok"] is False
    call(base, "/api/close", {"sid": sid})
