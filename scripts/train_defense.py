"""守備モデルの学習。自己対戦で「相手が聴牌しているか」「どの牌が当たり牌か」の正解付きデータを集め、
ロジスティック回帰(numpy)で学習して majai/defense_model.json に保存する。
  python scripts/train_defense.py --games 400
旧ヒューリスティック(agent.tile_danger)との比較(AUC・対数損失)も表示する。"""
import argparse, json, math, os, random, sys, time
from multiprocessing import Pool
import numpy as np
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from majai import defense as D
from majai.agent import tile_danger
from majai.engine import play_game
from majai.params import Params
from majai.players import AIPlayer, EfficiencyPlayer, EfficiencyPlusPlayer
from majai.tiles import str_to_idx


def collect(seed):
    rng = random.Random(seed)
    makers = [lambda: AIPlayer(), EfficiencyPlusPlayer, EfficiencyPlayer, lambda: AIPlayer(Params())]
    players = [rng.choice(makers)() for _ in range(4)]
    tile_rows, tp_rows = [], []

    def hook(rd, trackers):
        for p in range(4):
            if rd.riichi[p] and False:
                continue
            if rng.random() > 0.35:
                continue
            q = rng.choice([x for x in range(4) if x != p])
            st = trackers[q]
            waits = rd.waits(p)
            tenpai = 1.0 if waits else 0.0
            if not st.riichi[p]:
                tp_rows.append((D.tenpai_features(st, p), tenpai))
            if waits:
                vis = st.visible_counts()
                kind = 0 if st.riichi[p] else 1
                for t in sorted({str_to_idx(x) for x in st.hand}):
                    if t in st.genbutsu(p):
                        continue
                    tile_rows.append((kind, D.tile_features(st, p, t, vis), 1.0 if t in waits else 0.0,
                                      tile_danger(st, t, p, vis)))
    play_game(players, seed=seed, hook=hook)
    return tile_rows, tp_rows


def fit(X, y, l2=1e-4, epochs=60, lr=0.05, seed=0):
    rng = np.random.default_rng(seed)
    w = np.zeros(X.shape[1])
    m, v = np.zeros_like(w), np.zeros_like(w)
    n = len(y)
    t = 0
    for ep in range(epochs):
        idx = rng.permutation(n)
        for s in range(0, n, 2048):
            b = idx[s:s + 2048]
            z = X[b] @ w
            g = X[b].T @ (1 / (1 + np.exp(-z)) - y[b]) / len(b) + l2 * w
            t += 1
            m = 0.9 * m + 0.1 * g
            v = 0.999 * v + 0.001 * g * g
            w -= lr * (m / (1 - 0.9 ** t)) / (np.sqrt(v / (1 - 0.999 ** t)) + 1e-8)
    return w


def auc(score, y):
    order = np.argsort(score)
    ranks = np.empty(len(score))
    ranks[order] = np.arange(1, len(score) + 1)
    pos = y == 1
    npos, nneg = pos.sum(), (~pos).sum()
    return (ranks[pos].sum() - npos * (npos + 1) / 2) / (npos * nneg)


def logloss(p, y):
    p = np.clip(p, 1e-4, 1 - 1e-4)
    return float(-(y * np.log(p) + (1 - y) * np.log(1 - p)).mean())


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--games", type=int, default=400)
    ap.add_argument("--save-data", help="収集データをnpzに保存")
    a = ap.parse_args()
    t0 = time.time()
    with Pool(4) as pool:
        out = pool.map(collect, range(20000, 20000 + a.games), chunksize=4)
    tile_rows = [r for o in out for r in o[0]]
    tp_rows = [r for o in out for r in o[1]]
    print(f"データ収集 {time.time() - t0:.0f}s  牌サンプル={len(tile_rows)} 聴牌サンプル={len(tp_rows)}")
    if a.save_data:
        np.savez_compressed(a.save_data,
                            tile_kind=np.array([r[0] for r in tile_rows], dtype=np.int8),
                            tile_x=np.array([r[1] for r in tile_rows], dtype=np.float32),
                            tile_y=np.array([r[2] for r in tile_rows], dtype=np.float32),
                            tile_old=np.array([r[3] for r in tile_rows], dtype=np.float32),
                            tp_x=np.array([r[0] for r in tp_rows], dtype=np.float32),
                            tp_y=np.array([r[1] for r in tp_rows], dtype=np.float32))
    model = {"tile": {}, "tenpai": {}, "features": {"tile": D.TILE_FEATURES, "tenpai": D.TENPAI_FEATURES}}
    # 聴牌確率
    X = np.array([r[0] for r in tp_rows]); y = np.array([r[1] for r in tp_rows])
    cut = int(len(y) * 0.8)
    w = fit(X[:cut], y[:cut])
    p = 1 / (1 + np.exp(-(X[cut:] @ w)))
    print(f"[聴牌確率] 正解率(聴牌割合)={y.mean():.3f} 検証 AUC={auc(p, y[cut:]):.3f} logloss={logloss(p, y[cut:]):.4f} "
          f"(定数予測 {logloss(np.full_like(p, y[:cut].mean()), y[cut:]):.4f})")
    w = fit(X, y)
    model["tenpai"] = {"w": w.tolist()}
    for kind, name in ((0, "riichi"), (1, "other")):
        rows = [r for r in tile_rows if r[0] == kind]
        X = np.array([r[1] for r in rows]); y = np.array([r[2] for r in rows]); old = np.array([r[3] for r in rows])
        cut = int(len(y) * 0.8)
        w = fit(X[:cut], y[:cut])
        p = 1 / (1 + np.exp(-(X[cut:] @ w)))
        yt = y[cut:]
        print(f"[当たり牌:{name}] n={len(y)} 当たり率={y.mean():.3f} 検証 新AUC={auc(p, yt):.3f} logloss={logloss(p, yt):.4f}"
              f" / 旧ヒューリスティック AUC={auc(old[cut:], yt):.3f} logloss={logloss(old[cut:], yt):.4f}")
        w = fit(X, y)
        model["tile"][name] = {"w": w.tolist()}
    with open(D.MODEL_PATH, "w") as f:
        json.dump(model, f)
    print("saved", D.MODEL_PATH)
