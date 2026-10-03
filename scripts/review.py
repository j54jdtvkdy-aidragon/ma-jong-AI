"""牌譜レビュー。
  python scripts/review.py game.jsonl --player 0        # mjai形式の牌譜を指定席視点でレビュー
  python scripts/review.py --demo --seed 5              # 自己対戦を1局生成し、席0をレビュー
"""
import argparse, json, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from majai.review import review_events, format_report

ap = argparse.ArgumentParser()
ap.add_argument("log", nargs="?")
ap.add_argument("--player", type=int, default=0)
ap.add_argument("--top", type=int, default=10)
ap.add_argument("--all", action="store_true")
ap.add_argument("--demo", action="store_true")
ap.add_argument("--seed", type=int, default=0)
a = ap.parse_args()
if a.demo:
    from majai.engine import play_game
    from majai.players import RandomPlayer, EfficiencyPlayer
    # 席0を「素朴な人間っぽい打ち手」に見立ててレビュー
    r = play_game([EfficiencyPlayer()] + [EfficiencyPlayer() for _ in range(3)], seed=a.seed)
    events = r["events"]
    with open("demo_game.jsonl", "w") as f:
        for e in events:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
else:
    events = [json.loads(l) for l in open(a.log) if l.strip()]
print(format_report(review_events(events, a.player), top=a.top, show_all=a.all))
