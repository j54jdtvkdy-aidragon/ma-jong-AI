"""麻雀の打牌トレーニングアプリ(ローカルサーバー)。標準ライブラリのみ。
  python -m app.server            # http://127.0.0.1:8765 をブラウザで開く
AIと対局し、自分が打牌・鳴き・カンをするたびに、AIの推奨との比較と理由が表示される。"""
import argparse
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from app.session import Session, OPPONENTS  # noqa: E402

STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
SESSIONS = {}
LOCK = threading.Lock()
MAX_SESSIONS = 20


def _gc():
    now = time.time()
    for sid in [k for k, s in SESSIONS.items() if now - s.created > 4 * 3600]:
        SESSIONS.pop(sid).close()
    while len(SESSIONS) > MAX_SESSIONS:
        oldest = min(SESSIONS, key=lambda k: SESSIONS[k].created)
        SESSIONS.pop(oldest).close()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def _json(self, obj, code=200):
        data = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        try:
            return json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            return {}

    def do_GET(self):
        u = urlparse(self.path)
        if u.path in ("/", "/index.html"):
            with open(os.path.join(STATIC, "index.html"), "rb") as f:
                data = f.read()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        elif u.path == "/api/state":
            q = parse_qs(u.query)
            s = SESSIONS.get((q.get("sid") or [""])[0])
            if not s:
                return self._json({"error": "no such session"}, 404)
            ev = int((q.get("ev") or ["0"])[0])
            fb = int((q.get("fb") or ["0"])[0])
            # 変化があるか、最大1.5秒まで待つ(ポーリングの負荷を下げる)
            end = time.time() + 1.5
            while time.time() < end:
                with s.cv:
                    if s.pending is not None or s.result is not None or s.error or len(s.events) > ev:
                        break
                time.sleep(0.05)
            self._json(s.snapshot(ev, fb))
        elif u.path == "/favicon.ico":
            self.send_response(204)
            self.end_headers()
        elif u.path == "/api/opponents":
            self._json({k: v[0] for k, v in OPPONENTS.items()})
        else:
            self.send_error(404)

    def do_POST(self):
        u = urlparse(self.path)
        body = self._body()
        if u.path == "/api/new":
            with LOCK:
                _gc()
                s = Session(opponent=body.get("opponent", "medium"), east_only=bool(body.get("east_only", True)))
                SESSIONS[s.id] = s
            self._json({"sid": s.id})
        elif u.path == "/api/action":
            s = SESSIONS.get(body.get("sid"))
            if not s:
                return self._json({"error": "no such session"}, 404)
            ok = s.act(body.get("id"), body)
            self._json({"ok": ok})
        elif u.path == "/api/close":
            s = SESSIONS.pop(body.get("sid"), None)
            if s:
                s.close()
            self._json({"ok": True})
        else:
            self.send_error(404)


def make_server(host="127.0.0.1", port=8765):
    return ThreadingHTTPServer((host, port), Handler)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    a = ap.parse_args()
    srv = make_server(a.host, a.port)
    print(f"http://{a.host}:{a.port} をブラウザで開いてください (Ctrl+Cで終了)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
