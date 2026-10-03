"""AIの調整可能パラメータ。scripts/tune.py で探索し、結果は majai/best_params.json に保存される。"""
import json
import os
from dataclasses import dataclass, asdict, fields

_BEST = os.path.join(os.path.dirname(os.path.abspath(__file__)), "best_params.json")


@dataclass
class Params:
    # 打点・和了率の見積り
    riichi_extra_han: float = 0.5     # 立直による打点上乗せ(1翻に加えて、裏ドラ・一発の期待)
    menzen_yaku_han: float = 0.4      # 門前の平和・ツモ等の期待翻
    yakuhai_pair_han: float = 0.35    # 役牌対子の期待翻
    value_scale: float = 1.0          # 打点全体の倍率(大きいほど攻撃的)
    pwin_scale: float = 0.85          # 和了確率の倍率
    open_boost: float = 1.35          # 副露手の進みやすさ
    riichi_pw_mult: float = 1.05      # 立直による和了率の倍率
    # 守備
    danger_scale: float = 1.0         # 牌の危険度の倍率
    loss_scale: float = 1.0           # 放銃時の失点の倍率
    future_scale: float = 1.0         # 押し続けた場合の将来放銃リスクの倍率
    fold_cost: float = 600.0          # 降りる場合の固定コスト(点)
    fold_risk: float = 0.04           # 降りても放銃する確率(×失点)
    open_threat3: float = 0.5         # 3副露以上の仕掛けの脅威度
    open_threat2: float = 0.35        # ドラ2以上の2副露の脅威度
    # 鳴き・カン
    call_mult: float = 1.15           # 鳴く場合のEV倍率しきい値
    call_add: float = 150.0           # 鳴く場合のEV加算しきい値(点)
    kan_max_shanten: int = 2          # このシャンテン以下のときのみカン
    kan_enable: int = 1               # 0でカンしない
    # モンテカルロ和了率 (mc_rollouts=0 で無効=従来の幾何分布近似)
    mc_rollouts: int = 0              # 候補ごとのシミュレーション回数
    mc_max_shanten: int = 2           # このシャンテン以下の候補にだけ使う
    mc_scale: float = 1.0             # MC和了率の倍率
    mc_hazard: float = 0.16           # 毎巡、他家の和了などで局が終わる確率(自己対戦で較正)
    mc_ron_riichi: float = 0.8        # 立直時に、待ち牌が他家から出たときロンできる割合(較正)
    mc_ron_dama: float = 0.08         # ダマ/手替わり時の同割合(較正)
    # 強い素朴bot(efficiency+)の戦術。既定はすべてオフ(=従来の挙動)
    riichi_bias: float = 0.0          # 立直候補のEVに加算する点数(大きいほど聴牌即立直に近づく)
    fold_shanten: int = 99            # 立直者がいて、最善でもこのシャンテン以上なら強制オリ(1=efficiency+と同じ)
    push_danger_limit: float = 1.0    # 立直者に聴牌で押すとき、放銃率がこれを超える牌は切らない(0.12=efficiency+)
    yakuhai_pon_force: int = 0        # 1: 役牌は脅威がなければEVに関わらずポン
    sure_yaku_call_force: int = 0     # 1: 役が確定した副露手/タンヤオ形でシャンテンが進む鳴きは常にする
    # 順位を意識した調整 (南3局以降)
    lead_defense: float = 1.0         # トップ目での放銃失点の倍率
    trail_aggr: float = 1.0           # 4位での打点の倍率

    def to_json(self, path):
        with open(path, "w") as f:
            json.dump(asdict(self), f, indent=1, ensure_ascii=False)

    @classmethod
    def from_dict(cls, d):
        names = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in names})

    @classmethod
    def load(cls, path):
        with open(path) as f:
            return cls.from_dict(json.load(f))

    @classmethod
    def best(cls):
        """チューニング済みがあればそれを、無ければ既定値。"""
        if os.path.exists(_BEST):
            return cls.load(_BEST)
        return cls()


DEFAULT = Params()
