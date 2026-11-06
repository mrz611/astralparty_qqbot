"""Query orchestration: protocol adapters -> report model -> text/image."""
from dataclasses import dataclass
from datetime import datetime
import bot_ext
from stats_report import build_report, format_report, BEIJING


@dataclass(frozen=True)
class QueryResult:
    text: str
    image: bytes | None = None


class StatsQuery:
    def __init__(self, manager, images=False):
        self.manager = manager
        self.images = images

    def mine(self, openid, view='overview', hero='', battle_type='pve', page=1):
        profile, error = self.manager.get_profile(openid)
        if profile is None:
            return QueryResult(error)
        return self.present(profile, view, hero, battle_type, page)

    def player(self, uid, requester=None, view='overview', hero='', battle_type='pve', page=1):
        if view == 'recent':
            return QueryResult('公开 UID 查询没有对局时间，无法查询今日战绩。请登录自己的账号后使用 /atp me recent。')
        result = bot_ext.query_uid(self.manager, uid, prefer_uid=requester)
        if not result.get('ok'):
            return QueryResult(result.get('msg') or '查询失败')
        return self.present(bot_ext.public_profile(uid, result), view, hero, battle_type, page)

    def present(self, profile, view='overview', hero='', battle_type='pve', page=1):
        # Text and image use exactly the same sample and missing-data policy.
        rows = list(profile.get('recent', []))
        now = datetime.now(BEIJING)
        try:
            report = build_report(profile, rows, view, hero, battle_type, now=now, page=page)
        except ValueError as error:
            return QueryResult(str(error))
        if self.images and not report.cumulative:
            enriched = bot_ext.replay_rows(dict(profile, recent=report.display_rows))
            combined = list(report.rows)
            start = (report.page - 1) * report.page_size
            combined[start:start + len(enriched)] = enriched
            enriched_report = build_report(profile, combined, view, hero, battle_type, now=now, page=page)
            enriched_report.sample_count = report.sample_count
            enriched_report.missing_time = report.missing_time
            report = enriched_report
        text = format_report(report)
        image = None
        if self.images:
            try:
                if getattr(self, 'usage_card', False) and profile.get("usage"):
                    from bot_usage import render_usage_card_png
                    image = render_usage_card_png(profile)
                else:
                    from bot_viz import render_report_png
                    image = render_report_png(report)
            except Exception as error:
                print('[stats] 图片生成失败，回退文字:', type(error).__name__)
        return QueryResult(text, image)
