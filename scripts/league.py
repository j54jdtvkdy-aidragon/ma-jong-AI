"""チャンピオン更新リーグ。
  python scripts/league.py --gens 3
各世代: (1) チャンピオン3人を相手に挑戦者のパラメータを探索 → (2) 探索に使っていない配牌で
「挑戦者1人 vs チャンピオン3人」を対戦(互角なら平均2.5位)し、有意に勝てば候補にする →
(3) 強い素朴bot(efficiency+)に対して悪化していないか確認(チャンピオンだけに特化した弱点つぶしを防ぐ) →
通ったら新チャンピオン(majai/best_params.json と champions/gen_N.json に保存)。
状態は league_state.json に保存され、--resume で世代単位で再開できる。"""
import argparse, json, os, random, sys, time
from dataclasses import asdict
from multiprocessing import Pool
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))
from evaluate import evaluate
from tune import SPACE, sample, to_unit, from_unit
from majai.params import Params, _BEST

STATE, LOG = "league_state.json", "league_log.jsonl"
T0 = time.time()


def log(rec):
    rec["t"] = round(time.time() - T0)
    with open(LOG, "a") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    print(json.dumps({k: v for k, v in rec.items() if k != "params"}, ensure_ascii=False), flush=True)


def vs_champ(pool, p, champ, games, seed0):
    return evaluate(games, "majai", "champ", asdict(p), seed0=seed0, pool=pool, opp_params=asdict(champ))


def vs_plus(pool, p, games, seed0):
    return evaluate(games, "majai", "efficiency+", asdict(p), seed0=seed0, pool=pool)


def search(pool, champ, gen, a, rng):
    """champを相手に挑戦者を探索し、最良の挑戦者を返す。"""
    center = champ
    for rd in range(a.rounds):
        sd = a.sd * (0.8 ** rd)
        base = 1_000_000 * (gen + 1) + rd * 10000
        cands = [("center", center)] + [(f"c{i}", sample(center, sd, rng)) for i in range(a.pop)]
        res = []
        for name, p in cands:
            r = vs_champ(pool, p, champ, a.g1, base)
            res.append([name, p, r["avg_rank"] * a.g1, a.g1])
            log({"stage": 1, "gen": gen, "round": rd, "cand": name, "avg_rank": round(r["avg_rank"], 3)})
        res.sort(key=lambda x: x[2] / x[3])
        keep = res[:max(4, a.pop // 4)]
        for stage, games, seed_off in ((2, a.g2, 2000), (3, a.g3, 4000)):
            for item in keep:
                r = vs_champ(pool, item[1], champ, games, base + seed_off)
                item[2] += r["avg_rank"] * games
                item[3] += games
                log({"stage": stage, "gen": gen, "round": rd, "cand": item[0],
                     "avg_rank_cum": round(item[2] / item[3], 3)})
            keep.sort(key=lambda x: x[2] / x[3])
            keep = keep[:3]
        win = keep[0]
        d = asdict(win[1])
        if len(keep) > 1:
            for n in SPACE:
                d[n] = from_unit(n, 0.65 * to_unit(n, asdict(keep[0][1])[n]) + 0.35 * to_unit(n, asdict(keep[1][1])[n]))
        center = Params.from_dict(d)
        log({"stage": "round_best", "gen": gen, "round": rd, "cand": win[0],
             "avg_rank_cum": round(win[2] / win[3], 3)})
    return center


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gens", type=int, default=3)
    ap.add_argument("--rounds", type=int, default=2)
    ap.add_argument("--pop", type=int, default=20)
    ap.add_argument("--g1", type=int, default=100)
    ap.add_argument("--g2", type=int, default=300)
    ap.add_argument("--g3", type=int, default=600)
    ap.add_argument("--val", type=int, default=2000)
    ap.add_argument("--sd", type=float, default=0.15)
    ap.add_argument("--resume", action="store_true")
    a = ap.parse_args()
    champ, gen, hist = Params.best(), 0, []
    if a.resume and os.path.exists(STATE):
        st = json.load(open(STATE))
        champ, gen, hist = Params.from_dict(st["champion"]), st["gen"], st["history"]
        print(f"再開: 第{gen}世代から")
    os.makedirs("champions", exist_ok=True)
    pool = Pool(4)
    while gen < a.gens:
        rng = random.Random(1000 + gen)
        chall = search(pool, champ, gen, a, rng)
        vs0 = 9_000_000 + gen * 100000
        r = vs_champ(pool, chall, champ, a.val, vs0)
        win = r["avg_rank"] < 2.5 - 1.5 * r["se"]
        # 強い素朴botへの悪化チェック(同じ配牌で チャンピオン と 挑戦者)
        rp_c = vs_plus(pool, champ, 800, vs0 + 50000)
        rp_n = vs_plus(pool, chall, 800, vs0 + 50000)
        robust = rp_n["avg_rank"] <= rp_c["avg_rank"] + 0.06
        log({"stage": "gen_result", "gen": gen, "vs_champ": round(r["avg_rank"], 3), "se": round(r["se"], 3),
             "dist": [round(x, 3) for x in r["dist"]], "champ_vs_eff+": round(rp_c["avg_rank"], 3),
             "chall_vs_eff+": round(rp_n["avg_rank"], 3), "accepted": bool(win and robust),
             "params": asdict(chall)})
        if win and robust:
            champ = chall
            champ.to_json(f"champions/gen_{gen + 1}.json")
            champ.to_json(_BEST)
            hist.append({"gen": gen + 1, "vs_prev_champ": r["avg_rank"], "vs_eff+": rp_n["avg_rank"]})
        else:
            hist.append({"gen": gen + 1, "rejected": True, "vs_prev_champ": r["avg_rank"]})
        gen += 1
        json.dump({"champion": asdict(champ), "gen": gen, "history": hist}, open(STATE, "w"), ensure_ascii=False)


if __name__ == "__main__":
    main()
