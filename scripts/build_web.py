"""オフライン対応のスマホ向けWebアプリ(PWA)を docs/play/ に生成する。
  python scripts/build_web.py
- Pyodide(ブラウザで動くPython)を同梱し、majai/app のPythonコードと mahjong ライブラリをzipにして配る。
- service worker が全ファイルをキャッシュするので、一度開けば機内モードでも動く(HTTPSまたはlocalhostで配信すること)。
- GitHub Pages なら Settings → Pages → Branch: master / フォルダ: /docs を選ぶと https://<ユーザー>.github.io/<リポジトリ>/play/ で開ける。"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
OUT = os.path.join(ROOT, "docs", "play")
PYODIDE_VERSION = "314.0.7"
PYODIDE_FILES = ["pyodide.js", "pyodide.asm.mjs", "pyodide.asm.wasm", "python_stdlib.zip", "pyodide-lock.json"]


def get_pyodide():
    dst = os.path.join(OUT, "pyodide")
    if all(os.path.exists(os.path.join(dst, f)) for f in PYODIDE_FILES):
        return
    os.makedirs(dst, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run(["npm", "pack", f"pyodide@{PYODIDE_VERSION}"], cwd=tmp, check=True, capture_output=True)
        tgz = next(f for f in os.listdir(tmp) if f.endswith(".tgz"))
        subprocess.run(["tar", "xzf", tgz], cwd=tmp, check=True)
        for f in PYODIDE_FILES:
            shutil.copy(os.path.join(tmp, "package", f), os.path.join(dst, f))
        lic = os.path.join(tmp, "package", "LICENSE")
        if os.path.exists(lic):
            shutil.copy(lic, os.path.join(dst, "LICENSE"))


def build_bundle():
    import mahjong
    mj_dir = os.path.dirname(mahjong.__file__)
    os.makedirs(os.path.join(OUT, "bundle"), exist_ok=True)
    path = os.path.join(OUT, "bundle", "py.zip")
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        def add_tree(src, arc_root, exts):
            for d, _, fs in os.walk(src):
                if "__pycache__" in d or "tests" in d.split(os.sep):
                    continue
                for f in sorted(fs):
                    if f.endswith(exts):
                        full = os.path.join(d, f)
                        z.write(full, os.path.join(arc_root, os.path.relpath(full, src)))
        add_tree(os.path.join(ROOT, "majai"), "majai", (".py", ".json"))
        for f in ("__init__.py", "session.py", "local.py"):
            z.write(os.path.join(ROOT, "app", f), os.path.join("app", f))
        add_tree(mj_dir, "mahjong", (".py",))
    return path


ICON_SVG = """<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 512 512'><rect width='512' height='512' fill='#0f3825'/>
<rect x='130' y='80' width='252' height='352' rx='36' fill='#f6f1e1' stroke='#cfc7ad' stroke-width='10'/>
<text x='256' y='290' font-size='210' text-anchor='middle' fill='#c8281e' font-family='sans-serif' font-weight='700'>中</text>
<rect x='130' y='388' width='252' height='44' rx='22' fill='#1c7a3e'/></svg>"""


def make_icons():
    d = os.path.join(OUT, "icons")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "icon.svg"), "w") as f:
        f.write(ICON_SVG)
    if all(os.path.exists(os.path.join(d, n)) for n in ("icon-192.png", "icon-512.png", "apple-touch-icon.png")):
        return
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            b = p.chromium.launch(executable_path=os.environ.get("CHROMIUM", "/opt/pw-browsers/chromium"))
            for name, size in (("icon-192.png", 192), ("icon-512.png", 512), ("apple-touch-icon.png", 180)):
                pg = b.new_page(viewport={"width": size, "height": size})
                svg = ICON_SVG.replace("<svg ", '<svg width="%d" height="%d" ' % (size, size))
                pg.set_content("<body style='margin:0'>" + svg + "</body>")
                pg.screenshot(path=os.path.join(d, name))
            b.close()
    except Exception as e:
        print("アイコンPNGの生成をスキップ:", e)


MANIFEST = {
    "name": "麻雀 打牌トレーニング", "short_name": "麻雀トレ", "start_url": "./", "scope": "./",
    "display": "standalone", "background_color": "#0f1f17", "theme_color": "#0a150f", "lang": "ja",
    "icons": [{"src": "icons/icon-192.png", "sizes": "192x192", "type": "image/png"},
              {"src": "icons/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any maskable"}],
}

BOOT_JS = r"""
// ブラウザ内Pythonの起動。進捗を #boot に表示する。
window.MJ_BOOT = async function(){
  const box = document.getElementById("boot"), msg = document.getElementById("bootmsg"), bar = document.getElementById("bootbar");
  const step = (t, p) => { msg.textContent = t; bar.style.width = p + "%"; };
  step("エンジンを読み込み中…(初回のみ約14MB。以降はオフラインで使えます)", 5);
  const pyodide = await loadPyodide({indexURL: "./pyodide/"});
  step("AIを読み込み中…", 60);
  const buf = await (await fetch("./bundle/py.zip")).arrayBuffer();
  pyodide.unpackArchive(buf, "zip");
  pyodide.runPython("import sys; sys.path.insert(0, '/home/pyodide')");
  step("起動中…", 85);
  const L = pyodide.pyimport("app.local");
  step("準備完了", 100);
  setTimeout(() => box.style.display = "none", 250);
  return L;
};
if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register("./sw.js").catch(e => console.warn("service worker:", e));
}
"""

SW_JS = """
const CACHE = "mj-trainer-%(version)s";
const FILES = %(files)s;
self.addEventListener("install", e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(FILES)).then(() => self.skipWaiting()));
});
self.addEventListener("activate", e => {
  e.waitUntil(caches.keys().then(ks => Promise.all(ks.filter(k => k !== CACHE).map(k => caches.delete(k)))).then(() => self.clients.claim()));
});
self.addEventListener("fetch", e => {
  if (e.request.method !== "GET") return;
  e.respondWith(caches.match(e.request, {ignoreSearch: true}).then(r => r || fetch(e.request)));
});
"""


def build_index():
    with open(os.path.join(ROOT, "app", "static", "index.html"), encoding="utf-8") as f:
        html = f.read()
    head = ('<link rel="icon" href="icons/icon.svg" type="image/svg+xml">\n<link rel="manifest" href="manifest.webmanifest">\n<meta name="theme-color" content="#0a150f">\n'
            '<link rel="apple-touch-icon" href="icons/apple-touch-icon.png">\n<meta name="apple-mobile-web-app-capable" content="yes">\n'
            '<script src="pyodide/pyodide.js"></script>\n<script src="boot.js"></script>\n'
            '<style>#boot{position:fixed;inset:0;background:#0f1f17;color:#f1ecd8;display:flex;flex-direction:column;'
            'align-items:center;justify-content:center;gap:14px;z-index:99;padding:24px;text-align:center}'
            '#boot .b{width:min(320px,80vw);height:8px;background:#ffffff22;border-radius:4px;overflow:hidden}'
            '#boot .b>div{height:100%;background:#e0b94a;width:0;transition:width .3s}</style>\n')
    html = html.replace("</head>", head + "</head>", 1)
    html = html.replace("<body>", '<body>\n<div id="boot"><div style="font-size:18px;font-weight:600">麻雀 打牌トレーニング</div>'
                        '<div id="bootmsg">起動中…</div><div class="b"><div id="bootbar"></div></div></div>', 1)
    # スマホ向け: 解説パネルを盤面の下に出す既存のレスポンシブ設定をそのまま使う
    return html


def main():
    os.makedirs(OUT, exist_ok=True)
    get_pyodide()
    build_bundle()
    make_icons()
    with open(os.path.join(OUT, "index.html"), "w", encoding="utf-8") as f:
        f.write(build_index())
    with open(os.path.join(OUT, "boot.js"), "w", encoding="utf-8") as f:
        f.write(BOOT_JS)
    with open(os.path.join(OUT, "manifest.webmanifest"), "w", encoding="utf-8") as f:
        json.dump(MANIFEST, f, ensure_ascii=False, indent=1)
    files = []
    for d, _, fs in os.walk(OUT):
        for f in fs:
            rel = os.path.relpath(os.path.join(d, f), OUT)
            if rel not in ("sw.js",) and f != "LICENSE":
                files.append("./" + rel.replace(os.sep, "/"))
    files = sorted(set(files)) + ["./"]
    h = hashlib.sha256()
    for rel in sorted(files):
        p = os.path.join(OUT, rel[2:]) if rel != "./" else os.path.join(OUT, "index.html")
        h.update(rel.encode())
        if os.path.exists(p) and "pyodide/" not in rel:
            with open(p, "rb") as f:
                h.update(f.read())
    with open(os.path.join(OUT, "sw.js"), "w") as f:
        f.write(SW_JS % {"version": h.hexdigest()[:12], "files": json.dumps(files)})
    total = sum(os.path.getsize(os.path.join(d, f)) for d, _, fs in os.walk(OUT) for f in fs)
    print(f"docs/play を生成しました ({total / 1e6:.1f} MB, {len(files)} ファイル)")


if __name__ == "__main__":
    main()
