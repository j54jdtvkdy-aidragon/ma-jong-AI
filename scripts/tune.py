"""パラメータ探索 (段階的絞り込み + 交差エントロピー法風の更新)。
  python scripts/tune.py --rounds 3 --pop 24
各ラウンド: 候補を中心の周りに生成 → 100局で上位1/4 → 300局で上位3 → 600局で最良を決定。
全候補・基準(現在の中心)に同じシード(同じ配牌)を使って運の差を減らす。
最後に、探索に使っていない新しいシードで 現在のbest vs 既定値 を検証し、勝った場合だけ best_params.json を更新する。"""
import argparse, json, math, os, random, sys, time
from dataclasses import asdict
from multiprocessing import Pool
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))
from evaluate import evaluate
from majai.params import Params, _BEST

# name: (low, high, kind)  kind: lin / log / int
SPACE = {
    "riichi_extra_han": (0.0, 2.0, "lin"), "menzen_yaku_han": (0.0, 1.2, "lin"),
    "yakuhai_pair_han": (0.0, 0.8, "lin"), "value_scale": (0.6, 1.8, "log"),
    "pwin_scale": (0.5, 1.2, "lin"), "open_boost": (1.0, 2.0, "lin"),
    "riichi_pw_mult": (0.9, 1.3, "lin"), "danger_scale": (0.4, 2.5, "log"),
    "loss_scale": (0.5, 2.5, "log"), "future_scale": (0.0, 2.5, "lin"),
    "fold_cost": (0.0, 2000.0, "lin"), "fold_risk": (0.0, 0.15, "lin"),
    "open_threat3": (0.2, 1.0, "lin"), "open_threat2": (0.0, 0.8, "lin"),
    "call_mult": (0.8, 1.8, "lin"), "call_add": (-300.0, 800.0, "lin"),
    "kan_max_shanten": (0, 3, "int"), "kan_enable": (0, 1, "int"),
    "lead_defense": (0.8, 2.5, "log"), "trail_aggr": (0.8, 2.0, "log"),
}


def to_unit(name, v):
    lo, hi, k = SPACE[name]
    if k == "log":
        return (math.log(v) - math.log(lo)) / (math.log(hi) - math.log(lo))
    return (v - lo) / (hi - lo)


def from_unit(name, u):
    lo, hi, k = SPACE[name]
    u = min(1.0, max(0.0, u))
    if k == "log":
        return math.exp(math.log(lo) + u * (math.log(hi) - math.log(lo)))
    v = lo + u * (hi - lo)
    return int(round(v)) if k == "int" else v


def sample(center: Params, sd, rng):
    d = asdict(center)
    for n in SPACE:
        if rng.random() < 0.6:           # 一部の次元だけ動かす
            d[n] = from_unit(n, to_unit(n, d[n]) + rng.gauss(0, sd))
    return Params.from_dict(d)


def score(pool, p, games, seed0):
    r = evaluate(games, "majai", "mix", asdict(p), seed0=seed0, pool=pool)
    return r


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rounds", type=int, default=3)
    ap.add_argument("--pop", type=int, default=24)
    ap.add_argument("--g1", type=int, default=100)
    ap.add_argument("--g2", type=int, default=300)
    ap.add_argument("--g3", type=int, default=600)
    ap.add_argument("--val", type=int, default=1600)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--sd", type=float, default=0.18)
    ap.add_argument("--log", default="tuning_log.jsonl")
    a = ap.parse_args()
    rng = random.Random(a.seed)
    center = Params.best()
    default = Params()
    pool = Pool(4)
    t0 = time.time()

    def log(rec):
        rec["t"] = round(time.time() - t0)
        with open(a.log, "a") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        print(json.dumps({k: v for k, v in rec.items() if k != "params"}, ensure_ascii=False), flush=True)

    seed_base = 100000 * a.seed
    for rd in range(a.rounds):
        sd = a.sd * (0.8 ** rd)
        s1, s2, s3 = seed_base + rd * 10000, seed_base + rd * 10000 + 2000, seed_base + rd * 10000 + 4000
        cands = [("center", center)] + [(f"c{i}", sample(center, sd, rng)) for i in range(a.pop)]
        res = []
        for name, p in cands:
            r = score(pool, p, a.g1, s1)
            res.append([name, p, r["avg_rank"] * a.g1, a.g1])
            log({"stage": 1, "round": rd, "cand": name, "avg_rank": round(r["avg_rank"], 3), "se": round(r["se"], 3)})
        res.sort(key=lambda x: x[2] / x[3])
        keep = res[:max(4, a.pop // 4)]
        if all(k[0] != "center" for k in keep):
            keep.append(next(x for x in res if x[0] == "center"))
        for item in keep:
            r = score(pool, item[1], a.g2, s2)
            item[2] += r["avg_rank"] * a.g2
            item[3] += a.g2
            log({"stage": 2, "round": rd, "cand": item[0], "avg_rank_cum": round(item[2] / item[3], 3)})
        keep.sort(key=lambda x: x[2] / x[3])
        final = keep[:3]
        if all(k[0] != "center" for k in final):
            final.append(next(x for x in keep if x[0] == "center"))
        for item in final:
            r = score(pool, item[1], a.g3, s3)
            item[2] += r["avg_rank"] * a.g3
            item[3] += a.g3
            log({"stage": 3, "round": rd, "cand": item[0], "avg_rank_cum": round(item[2] / item[3], 3)})
        final.sort(key=lambda x: x[2] / x[3])
        win = final[0]
        log({"stage": "round_best", "round": rd, "cand": win[0], "avg_rank_cum": round(win[2] / win[3], 3),
             "params": asdict(win[1])})
        # 中心の更新: 上位候補の平均(勝者に引っ張られすぎないよう勝者を重めに)
        top = [f[1] for f in final[:2]]
        d = asdict(win[1])
        if len(top) > 1:
            for n in SPACE:
                u = 0.65 * to_unit(n, asdict(top[0])[n]) + 0.35 * to_unit(n, asdict(top[1])[n])
                d[n] = from_unit(n, u)
        center = Params.from_dict(d)
        center.to_json("tuning_center.json")

    # 検証: 新しいシードで center vs default vs 現行best を比較
    vs = a.seed * 100000 + 90000
    rc = score(pool, center, a.val, vs)
    rd_ = score(pool, default, a.val, vs)
    log({"stage": "validation", "center": round(rc["avg_rank"], 3), "center_se": round(rc["se"], 3),
         "default": round(rd_["avg_rank"], 3), "default_se": round(rd_["se"], 3), "params": asdict(center)})
    if rc["avg_rank"] < rd_["avg_rank"] - 0.5 * (rc["se"] ** 2 + rd_["se"] ** 2) ** 0.5:
        center.to_json(_BEST)
        print("best_params.json を更新しました")
    else:
        print("検証で既定値に有意に勝てなかったため best_params.json は更新しません")


if __name__ == "__main__":
    main()
