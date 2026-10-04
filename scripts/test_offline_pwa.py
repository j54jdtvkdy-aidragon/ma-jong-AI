"""オフライン動作の確認(要playwright+chromium)。 docs/play を静的配信して、オンラインで一度開いた後、ネットを切って再読込し、対局を最後まで通す。
  (cd docs/play && python3 -m http.server 8800 &) ; python3 scripts/test_offline_pwa.py"""
import asyncio, time
from playwright.async_api import async_playwright
URL="http://127.0.0.1:8800/"
async def play_some(pg, n):
    done=0; shot=False
    for _ in range(600):
        await pg.wait_for_timeout(200)
        if await pg.query_selector("#again"): return "finished"
        modal = await pg.query_selector(".modal")
        if modal:
            btns = await pg.query_selector_all(".modal .btn"); await btns[-1].click(); await pg.wait_for_timeout(300); continue
        if not await pg.query_selector("#status"): continue
        st = await pg.inner_text("#status")
        if "切る牌" in st:
            tiles = await pg.query_selector_all(".hand .tile:not(.dis)")
            await tiles[-1].click(); done += 1
            await pg.wait_for_timeout(300)
            if done >= n: return "ok"
    return "timeout"
async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch(executable_path="/opt/pw-browsers/chromium")
        ctx = await b.new_context(viewport={"width":390,"height":844}, device_scale_factor=2, is_mobile=True, has_touch=True)
        pg = await ctx.new_page()
        errs=[]; pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.on("console", lambda m: errs.append("console:"+m.text) if m.type=="error" else None)
        t=time.time()
        await pg.goto(URL)
        await pg.wait_for_function("document.getElementById('boot').style.display==='none'", timeout=120000)
        print("online boot sec:", round(time.time()-t,1))
        # service worker が有効化されキャッシュ完了するまで待つ
        await pg.wait_for_function("navigator.serviceWorker.ready.then(()=>true)", timeout=60000)
        n = await pg.evaluate("caches.keys().then(async ks => { const c = await caches.open(ks[0]); return (await c.keys()).length })")
        print("cached files:", n)
        await pg.reload(); await pg.wait_for_function("navigator.serviceWorker.controller!==null", timeout=30000)
        # --- オフラインにして開き直す ---
        await ctx.set_offline(True)
        t=time.time()
        await pg.goto(URL)
        await pg.wait_for_function("document.getElementById('boot').style.display==='none'", timeout=120000)
        print("OFFLINE boot sec:", round(time.time()-t,1))
        await pg.select_option("#opp","strong")
        await pg.click("#newBtn")
        await pg.wait_for_selector(".hand .tile", timeout=60000)
        await pg.screenshot(path="/tmp/claude-0/shots/m1_offline_start.png")
        r = await play_some(pg, 12)
        await pg.screenshot(path="/tmp/claude-0/shots/m2_offline_play.png", full_page=True)
        print("play:", r, "errors:", errs[:3])
        # 最後まで
        r = await play_some(pg, 10000)
        await pg.wait_for_timeout(500)
        await pg.screenshot(path="/tmp/claude-0/shots/m3_offline_result.png")
        print("full game offline:", r, "errors:", errs[:3])
        await b.close()
asyncio.run(main())
