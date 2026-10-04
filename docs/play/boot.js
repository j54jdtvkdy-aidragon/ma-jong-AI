
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
