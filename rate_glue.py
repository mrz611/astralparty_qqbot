# -*- coding: utf-8 -*-
"""星趴评分插件：/评分 16位回放号 → 四人单局评分卡。

通过 rate 包导入，不向 sys.path 插入 rate/，避免 rate/astral 抢占主 bot
的 astral 包并影响复盘渲染。RATE_PLUGIN=0 可禁用。
"""
import os
import re
import sys

_LOADED = False


def _route_rate(text, qb):
    match = re.match(r"^/评分\s+(\d{16})\s*$", text or "")
    if not match:
        return None
    try:
        from rate import rate_core

        result = rate_core.score_replay(match.group(1))
    except Exception as exc:
        return "评分出错：%s" % exc
    if isinstance(result, str):
        return result

    fallback = result.as_text()
    if not qb.CARD_BASE or all(player.score is None for player in result.players):
        return fallback
    try:
        from rate.score_viz import render_score_png

        return qb.Reply(fallback, render_score_png(result))
    except Exception as exc:
        print("[rate] 评分图生成失败，回退纯文字:", type(exc).__name__)
        return fallback


def mount():
    global _LOADED
    if _LOADED:
        return
    if os.environ.get("RATE_PLUGIN", "1") != "1":
        print("[rate] 插件已通过 RATE_PLUGIN=0 禁用")
        return
    try:
        from rate import rate_core  # noqa: F401  # 部署自检
    except Exception as exc:
        print("[rate] 评分核心不可用，插件跳过:", exc)
        return

    main = sys.modules.get("__main__")
    qb = main if (main is not None and hasattr(main, "handle_group")) else sys.modules["qq_bot"]
    orig_priv, orig_grp = qb.handle_private, qb.handle_group

    def _priv(text, uid):
        result = _route_rate(text, qb)
        return result if result is not None else orig_priv(text, uid)

    def _grp(text, mo):
        result = _route_rate(text, qb)
        return result if result is not None else orig_grp(text, mo)

    qb.handle_private = _priv
    qb.handle_group = _grp
    _LOADED = True
    print("[rate] 36角色量表插件已挂载（/评分 16位回放号）")
