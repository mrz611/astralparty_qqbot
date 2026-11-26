# -*- coding: utf-8 -*-
"""回放号 → 四名玩家的 36 角色单局表现量表评分。

使用 scales/ 中各角色冻结的 JSON + NPZ 参考分布。
"""
import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from .scales_core import ScaleBank, ScaleError

ROOT = Path(__file__).resolve().parent
CDN = "https://sereplaycn.feimogames.com/prod/"
CALIB_MAP = 82017
CALIB_DIFF = 3
TOTAL_COLS = ["dmg", "transfer", "move", "tret", "inj"]
KEY = ["replay_id", "uid"]
SCALE_VERSION = "v0.1"


@dataclass(frozen=True)
class PlayerRating:
    nick: str
    hero: str
    score: float | None = None
    percentile: float | None = None
    usage: str = ""
    error: str | None = None


@dataclass(frozen=True)
class ReplayRating:
    replay_id: str
    players: tuple[PlayerRating, ...]

    def as_text(self):
        lines = [f"回放 {self.replay_id}｜36角色单局量表 {SCALE_VERSION}"]
        for player in self.players:
            name = f"{player.nick}（{player.hero}）"
            if player.error is not None:
                lines.append(f"{name}：无法评分（{player.error}）")
                continue
            suffix = f"［{player.usage}］" if player.usage else ""
            lines.append(
                f"{name}：本局评分 {player.score:.2f}｜"
                f"同条件百分位 {player.percentile:.2f}%{suffix}"
            )
        lines.append("百分位按同角色、同地图、同难度冻结样本计算，不代表长期实力或胜率。")
        return "\n".join(lines)


def load_hero_names():
    for candidate in ROOT.rglob("heroes.json"):
        try:
            data = json.loads(candidate.read_text(encoding="utf-8"))
            return {int(entry["id"]): entry.get("name", "") for entry in data}
        except Exception:
            continue
    return {}


HERO_NAMES = load_hero_names()
HERO_NAMES.setdefault(130, "苍鲤")


def fetch_replay(replay_id):
    request = urllib.request.Request(
        CDN + str(replay_id), headers={"User-Agent": "Mozilla/5.0"}
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        raise RuntimeError(
            f"回放下载失败（HTTP {exc.code}）：服务器可能没有 {replay_id}"
        ) from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise RuntimeError(f"回放下载失败：{exc}") from exc


def parse_replay(bin_data, replay_id):
    """只使用主 bot 现有的 review_build/Dex 引擎，不启用第二套解析器。"""
    try:
        import review_build as rb
    except ImportError as exc:
        raise RuntimeError("未找到机器人现有的 review_build/Dex 解析引擎") from exc
    parsed = rb.build_review(bin_data, str(replay_id))
    parsed.setdefault("replay_id", str(replay_id))
    return _to_frames(parsed)


def _to_frames(parsed):
    replay_id = str(parsed["replay_id"])
    player_rows, round_rows = [], []
    for player in parsed.get("players") or []:
        totals = player.get("totals") or {}
        player_rows.append(
            {
                "replay_id": replay_id,
                "uid": player.get("uid"),
                "nick": player.get("nick"),
                "hero_id": player.get("hero_id"),
                "hero_name": HERO_NAMES.get(player.get("hero_id"), "")
                or f'#{player.get("hero_id")}',
                "map_id": parsed.get("map_id"),
                "difficulty": parsed.get("difficulty"),
                "rounds": parsed.get("rounds"),
                "died": totals.get("died", 0),
                "boss": totals.get("boss", 0),
                # Dex 已给出的全场总数是评分首选数据源；缺失时才用逐回合合计补位。
                "dmg": totals.get("dmg"),
                "transfer": totals.get("transfer"),
                "move": totals.get("move"),
                "tret": totals.get("tret"),
                "inj": totals.get("inj"),
            }
        )
        for round_row in player.get("rounds") or []:
            round_rows.append(
                {
                    "replay_id": replay_id,
                    "uid": player.get("uid"),
                    "round": round_row.get("round"),
                    "kill": round_row.get("kill"),
                    "dmg": round_row.get("dmg"),
                    "inj": round_row.get("inj"),
                    "gold": round_row.get("gold"),
                    "transfer": round_row.get("transfer"),
                    "tret": round_row.get("tret"),
                    "move": round_row.get("move"),
                    "deaths": round_row.get("deaths", 0),
                    "boss": int(bool(round_row.get("boss"))),
                }
            )
    return pd.DataFrame(player_rows), pd.DataFrame(round_rows)


def _merge_total_fields(features, rounds):
    """以 Dex totals 为全场总数基准，并与逐回合合计交叉核对。"""
    sums = rounds.groupby(KEY, as_index=False)[TOTAL_COLS].sum()
    sums = sums.rename(columns={key: f"_{key}_round_sum" for key in TOTAL_COLS})
    merged = features.merge(sums, on=KEY, how="left", validate="one_to_one")

    for key in TOTAL_COLS:
        sum_key = f"_{key}_round_sum"
        summed = pd.to_numeric(merged[sum_key], errors="coerce")
        if key in merged.columns:
            dex = pd.to_numeric(merged[key], errors="coerce")
        else:
            dex = pd.Series(float("nan"), index=merged.index)

        both = dex.notna() & summed.notna()
        mismatch = both & ((dex - summed).abs() > (1e-6 + dex.abs() * 1e-9))
        if mismatch.any():
            index = mismatch[mismatch].index[0]
            nick = merged.loc[index].get("nick") or merged.loc[index].get("uid")
            raise RuntimeError(
                f"Dex 总数与逐回合合计不一致：{nick} 的 {key} "
                f"（Dex={dex.loc[index]}，逐回合={summed.loc[index]}），已停止评分"
            )
        merged[key] = dex.fillna(summed)
        merged = merged.drop(columns=[sum_key])
    return merged


def _usage_label(result):
    """显示状态只读取元数据，不按角色名另写规则。"""
    confidence = result.get("sample_confidence")
    if confidence == "低":
        return result.get("usage_label") or "实验性"
    if confidence == "中":
        return "谨慎使用"
    return ""


def score_replay(replay_id, scales_path=None) -> ReplayRating | str:
    """计算供图片和文字共用的评分，或返回整局的不可评分原因。"""
    replay_id = str(replay_id)
    if not re.fullmatch(r"\d{16}", replay_id):
        raise RuntimeError("回放号应为 16 位数字")

    scale_root = Path(scales_path) if scales_path else ROOT / "scales"
    bank = ScaleBank(scale_root)
    data = fetch_replay(replay_id)
    players, rounds = parse_replay(data, replay_id)
    if players.empty or rounds.empty:
        return f"回放 {replay_id}：没有解析到可评分的玩家或回合数据"

    from .build_features import compute_features

    features = compute_features(players, rounds)
    # 全场总数优先取 Dex totals；逐回合合计用于交叉核对及缺失时补位。
    features = _merge_total_fields(features, rounds)
    if features.empty:
        return f"回放 {replay_id}：没有生成可评分的单局特征"

    try:
        map_id = int(features["map_id"].iloc[0])
        difficulty = int(features["difficulty"].iloc[0])
    except (TypeError, ValueError):
        return f"回放 {replay_id}：无法识别地图或难度，暂不能评分"
    if map_id != CALIB_MAP or difficulty != CALIB_DIFF:
        return (
            f"回放 {replay_id}：当前量表不适用这张对局\n"
            f"适用范围：地图 {CALIB_MAP}·难度 {CALIB_DIFF}；"
            f"本局：地图 {map_id}·难度 {difficulty}"
        )

    ratings = []
    for _, row in features.iterrows():
        nick = str(row.get("nick") or row.get("uid") or "未知玩家")
        hero = str(row.get("hero_name") or f'#{row.get("hero_id")}')
        try:
            result = bank.score_one(row.to_dict())
        except (ScaleError, KeyError, TypeError, ValueError) as exc:
            ratings.append(PlayerRating(nick, hero, error=str(exc)))
            continue
        ratings.append(PlayerRating(
            nick, result["hero_name"] or hero,
            score=100.0 * result["raw_score"],
            percentile=result["overall_percentile"],
            usage=_usage_label(result),
        ))
    return ReplayRating(replay_id, tuple(ratings))


def rate_replay(replay_id, scales_path=None):
    """保留原有的四人评分文本接口。"""
    result = score_replay(replay_id, scales_path)
    return result if isinstance(result, str) else result.as_text()


if __name__ == "__main__":
    import sys

    print(rate_replay(sys.argv[1]))
