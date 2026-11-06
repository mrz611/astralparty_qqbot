"""Pure statistics model shared by text and image presentation."""
from collections import defaultdict
from dataclasses import dataclass
from characters import CHARACTERS
from datetime import datetime, timedelta, timezone

BEIJING = timezone(timedelta(hours=8))


def average(rows, field):
    values = [r[field] for r in rows if r.get(field) is not None]
    return round(sum(values) / len(values)) if values else None


def summarize(rows):
    wins = sum(r.get('win') is True for r in rows)
    losses = sum(r.get('win') is False for r in rows)
    return {'count': len(rows), 'wins': wins, 'losses': losses,
            'unknown': len(rows) - wins - losses,
            'winrate': round(100 * wins / (wins + losses), 1) if wins + losses else None,
            'damage': average(rows, 'damage'), 'transfer': average(rows, 'transfer'),
            'damage_count': sum(r.get('damage') is not None for r in rows),
            'transfer_count': sum(r.get('transfer') is not None for r in rows)}


def normalize_rows(rows):
    result = []
    for row in rows:
        r = dict(row)
        rank = int(r.get('rank') or 0)
        if 'win' not in r:
            r['win'] = rank == 1 if rank > 0 else None
        r['hero'] = str(r.get('hero') or '未知角色')
        r.setdefault('mode', '未知模式')
        r.setdefault('damage', None)
        r.setdefault('transfer', None)
        result.append(r)
    return result


@dataclass
class StatsReport:
    profile: dict
    rows: list
    summary: dict
    heroes: list
    modes: list
    view: str
    hero: str
    sample_count: int
    battle_type: str
    date_label: str
    missing_time: int
    page: int = 1
    page_size: int = 20
    character_id: int | None = None
    character_info: dict | None = None
    kind: str = 'sample'

    @property
    def cumulative(self):
        return self.kind == 'career'

    @property
    def character_total(self):
        if not self.hero:
            return None
        if self.cumulative:
            return (self.character_info or {}).get('total',self.summary['count'])
        ident = self.character_id
        usage = self.profile.get('hero_usage') or {}
        if ident is not None and str(ident) in usage:
            return usage[str(ident)]
        for name, count in self.profile.get('usage', []):
            if CHARACTERS.matches({'hero': name}, ident, self.hero):
                return count
        return None

    @property
    def pages(self):
        count = len(self.heroes) if self.cumulative and self.view == 'overview' else len(self.rows)
        return max(1, (count + self.page_size - 1) // self.page_size)

    @property
    def display_rows(self):
        start = (self.page - 1) * self.page_size
        return self.rows[start:start + self.page_size]

    @property
    def display_heroes(self):
        start = (self.page - 1) * self.page_size
        return self.heroes[start:start + self.page_size]

    @property
    def scope(self):
        mode = {'pve': 'PVE', 'pvp': 'PVP', 'all': '全部模式'}[self.battle_type]
        if self.cumulative:
            return ('角色 · '+mode+(' · 已采集汇总（非完整累计）' if (self.character_info or {}).get('selected_source')=='observed' else ' · 累计')) if self.view == 'hero' else '账号累计 · ' + mode
        period = f'今日 {self.date_label}（UTC+8）' if self.view == 'recent' else '最近记录'
        source = '已保存及本次记录' if self.profile.get('history_source') == 'local' else '本次可用记录'
        return f'{period} · {mode} · {source} {self.sample_count} 条'


def build_sample_report(profile, rows, view='overview', hero='', battle_type='pve', now=None, page=1, page_size=20):
    if view not in ('overview', 'recent', 'hero'):
        raise ValueError('未知战绩视图')
    if battle_type not in ('pve', 'pvp', 'all'):
        raise ValueError('未知对局模式')
    normalized = normalize_rows(rows)
    sample_count = len(normalized)
    available_rows = normalized
    def matches(row):
        mode_id = row.get('mode_id')
        if mode_id is not None:
            return mode_id in (2, 4) if battle_type == 'pve' else mode_id == 1
        return row['mode'] == ('合作挑战' if battle_type == 'pve' else '标准对战')
    if battle_type != 'all':
        normalized = [row for row in normalized if matches(row)]
    date_label, missing_time = '', 0
    if view == 'recent':
        if now is not None and now.tzinfo is None:
            raise ValueError('筛选日期必须包含时区')
        local_now = now.astimezone(BEIJING) if now is not None else datetime.now(BEIJING)
        start = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=1)
        date_label = start.strftime('%Y-%m-%d')
        dated = []
        for row in normalized:
            try:
                timestamp = int(row.get('time') or 0)
            except (ValueError, TypeError, OverflowError):
                timestamp = 0
            if timestamp <= 0:
                missing_time += 1
            elif start.timestamp() <= timestamp < end.timestamp():
                dated.append(row)
        normalized = dated
    ident = None
    if view == 'hero':
        if not hero:
            raise ValueError('请指定角色名称')
        ident, hero = CHARACTERS.resolve(hero, available_rows)
        normalized = [r for r in normalized if CHARACTERS.matches(r, ident, hero)]
    grouped, modes = defaultdict(list), defaultdict(list)
    for row in normalized:
        grouped[row['hero']].append(row)
        modes[row['mode']].append(row)
    hero_stats = [(name, summarize(items)) for name, items in grouped.items()]
    hero_stats.sort(key=lambda item: (-item[1]['count'], item[0]))
    if not isinstance(page, int) or page < 1 or page_size < 1 or page_size > 50:
        raise ValueError('页码必须为正整数，每页最多 50 条。')
    pages = max(1, (len(normalized) + page_size - 1) // page_size)
    if page > pages:
        raise ValueError(f'页码超出范围，共 {pages} 页。')
    return StatsReport(dict(profile), normalized, summarize(normalized), hero_stats,
                       [(name, summarize(items)) for name, items in modes.items()],
                       view, hero, sample_count, battle_type, date_label, missing_time, page, page_size, ident)


def build_report(profile, rows, view='overview', hero='', battle_type='pve', now=None, page=1, page_size=20):
    if view == 'recent':
        return build_sample_report(profile, rows, view, hero, battle_type, now, page, page_size)
    if view not in ('overview','hero') or battle_type not in ('pve','pvp','all'):
        raise ValueError('未知战绩视图或模式')
    if not isinstance(page, int) or page < 1 or not 1 <= page_size <= 50:
        raise ValueError('页码必须为正整数，每页最多 50 条。')
    from career_stats import career_data
    summary, heroes, modes, ident, canonical, info = career_data(profile, view, hero, battle_type)
    pages = max(1, (len(heroes) + page_size - 1)//page_size) if view == 'overview' else 1
    if page > pages:
        raise ValueError(f'页码超出范围，共 {pages} 页。')
    return StatsReport(dict(profile), [], summary, heroes, modes, view, canonical, 0, battle_type,
                       '', 0, page, page_size, ident, info, 'career')


def format_career(report):
    p, stats = report.profile, report.summary
    title = ('角色'+report.battle_type.upper()+'总览') if report.view=='hero' else '个人'+report.battle_type.upper()+'总览'
    lines = [f"【{title}】{p.get('nick','未知玩家')}（UID {p.get('uid','—')}）", '口径：'+report.scope]
    if p.get('source') == 'public':
        lines.append('公开档案：累计数据未提供')
    if report.hero:
        lines.append(f'角色：{report.hero}（ID {report.character_id}）')
    measure = "已采集场次" if report.view=="hero" and (report.character_info or {}).get("selected_source")=="observed" else "累计场次"
    lines.append(f"{measure} {number(stats['count'])}｜胜场 {number(stats['wins'])}｜胜率 {percent(stats['winrate'])}")
    lines.append('模式拆分：')
    if report.view == 'hero':
        lines.append((report.character_info or {}).get('mode_note',''))
    for name, data in report.modes:
        lines.append(f"· {name}：{number(data['count'])} 场｜{number(data['wins'])} 胜｜{percent(data['winrate'])}")
    if report.view == 'overview':
        lines.append(f'角色数据（{report.battle_type.upper()}） 第 {report.page}/{report.pages} 页：')
        for name,data in report.display_heroes:
            lines.append(f"· {name}：{number(data['count'])} 场｜{number(data['wins'])} 胜｜{percent(data['winrate'])}")
    else:
        lines.append(f"角色等级 {number((report.character_info or {}).get('level'))}")
    lines.append('累计字段与已采集模式数据分别标注，未提供的累计指标显示 —。')
    if p.get('stale'):
        lines.append('部分资料来自已有登录档案。')
    return '\n'.join(lines)


def number(value):
    return '—' if value is None else f'{value:,}' if isinstance(value, int) else str(value)


def percent(value):
    return '—' if value is None else f'{value:.1f}%'


def format_report(report):
    if report.cumulative:
        return format_career(report)
    p, s = report.profile, report.summary
    label = {'overview': '战绩总览', 'recent': '今日战绩', 'hero': '角色战绩'}[report.view]
    lines = [f"【{label}】{p.get('nick', '未知玩家')}（UID {p.get('uid', '—')}）"]
    lines.append('范围：' + report.scope)
    lines.append(f'第 {report.page}/{report.pages} 页 · 每页 {report.page_size} 条 · 匹配总数 {len(report.rows)} 条')
    lines.append('历史仅含已获取的记录，筛选结果不保证覆盖当日全部对局；未获取的旧对局无法补回。')
    if report.missing_time:
        lines.append(f'{report.missing_time} 条记录缺少时间，无法归入今日，已排除。')
    if p.get('history_error'):
        lines.append('历史保存/读取失败，本次仅使用当前资料中的记录。')
    if p.get('records_error'):
        lines.append('近期对局获取失败，请稍后重试。')
    if p.get('stale'):
        lines.append('部分资料未能刷新，当前包含缓存数据。')
    if report.character_total is not None:
        lines.append(f'角色档案累计出场 {report.character_total} 场（档案口径，未分模式）。')
    if report.hero:
        lines.append(f'角色：{report.hero}（统计可用的 {report.sample_count} 条记录）')
    if report.battle_type == 'all' and report.view == 'overview' and p.get('total') is not None:
        lines.append(f"累计 {number(p['total'])} 场｜胜场 {number(p.get('wins'))}｜胜率 {percent(p.get('winrate'))}")
    elif p.get('source') == 'public':
        lines.append('公开档案：累计数据未提供')
    lines.append(f"匹配 {s['count']} 场｜{s['wins']} 胜 {s['losses']} 负｜胜率 {percent(s['winrate'])}")
    if s['unknown']:
        lines.append(f"其中 {s['unknown']} 场结果未知，不计入胜率")
    lines.append(f"场均伤害 {number(s['damage'])}（{s['damage_count']} 场）｜场均转账 {number(s['transfer'])}（{s['transfer_count']} 场）")
    for row in report.display_rows:
        outcome = '胜' if row['win'] is True else '负' if row['win'] is False else '未知'
        lines.append(f"· [{row['mode']}] {row['hero']}｜{outcome}｜伤害 {number(row['damage'])}｜转账 {number(row['transfer'])}")
    if not report.rows:
        lines.append('暂无符合条件的对局记录；不代表该角色从未出场，可尝试 all 或等待采集更多历史。')
    lines.append('— 为未提供数据；场均仅计算对应指标有数据的对局。')
    return '\n'.join(lines)
