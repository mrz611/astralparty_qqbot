# -*- coding: utf-8 -*-
"""战绩卡片渲染（PIL）。/我的 配图用。
依赖：Pillow + 中文字体（服务器需 apt install fonts-noto-cjk）。"""
import io
import os
from stats_report import build_report, average as _average, number as _num, percent
from PIL import Image, ImageDraw, ImageFont

_BOLD_CANDIDATES = [
    os.path.join(os.environ.get('WINDIR', 'C:/Windows'), 'Fonts', 'msyhbd.ttc'),
    '/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc',
    '/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc',
    '/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc',
]
_REG_CANDIDATES = [
    os.path.join(os.environ.get('WINDIR', 'C:/Windows'), 'Fonts', 'msyh.ttc'),
    '/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc',
    '/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc',
    '/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc',
]

def _find(cands):
    for p in cands:
        if os.path.exists(p):
            return p
    return None

_FP_BOLD = _find(_BOLD_CANDIDATES)
_FP_REG = _find(_REG_CANDIDATES)
_FCACHE = {}

def F(size, bold=False):
    key = (size, bold)
    if key not in _FCACHE:
        fp = _FP_BOLD if bold else _FP_REG
        if fp is None:
            raise RuntimeError('找不到中文字体：请 sudo apt install fonts-noto-cjk')
        _FCACHE[key] = ImageFont.truetype(fp, size)
    return _FCACHE[key]

# 中性数据报表：只用色彩区分指标，不依赖背景图或角色装饰。
INK = '#20262D'
MUTED = '#77808B'
GREEN = '#27851E'
PURPLE = '#7640AF'
RED = '#B84A4A'
LINE = '#DDE2E6'


def _fit(draw, text, width, size=24, bold=False):
    text = str(text)
    font = F(size, bold)
    if draw.textlength(text, font=font) <= width:
        return text, font
    while text and draw.textlength(text + '…', font=font) > width:
        text = text[:-1]
    return text + '…', font


def _cell(draw, bounds, y, text, color=INK, bold=False, left=False, size=24):
    x1, x2 = bounds
    text, font = _fit(draw, text, x2 - x1 - 28, size, bold)
    draw.text((x1 + 14 if left else (x1 + x2) / 2, y), text,
              font=font, fill=color, anchor='lm' if left else 'mm')


def _section(draw, y, title, note=''):
    draw.rectangle((40, y + 5, 46, y + 31), fill=INK)
    draw.text((60, y + 18), title, font=F(28, True), fill=INK, anchor='lm')
    if note:
        draw.text((1160, y + 18), note, font=F(18), fill=MUTED, anchor='rm')
    draw.line((40, y + 42, 1160, y + 42), fill=LINE, width=2)
    return y + 48


def _table_header(draw, y, columns, headings):
    draw.rectangle((40, y, 1160, y + 40), fill='#E8ECEF')
    for i, heading in enumerate(headings):
        _cell(draw, columns[i], y + 20, heading, MUTED, True, i == 0, 20)
    return y + 40


def _row(draw, y, columns, values, index, colors=None, bold=False):
    draw.rectangle((40, y, 1160, y + 46), fill='#F2F4F6' if index % 2 == 0 else '#FFFFFF')
    for i, value in enumerate(values):
        _cell(draw, columns[i], y + 23, value,
              (colors or {}).get(i, INK), bold or i == 0, i == 0)
    return y + 46


def render_career_report(report):
    p, stats = report.profile, report.summary
    role = report.view == 'hero'
    observed = (report.character_info or {}).get('selected_source')=='observed'
    mode_label = {'pve':'PVE','pvp':'PVP','all':'全模式'}[report.battle_type]
    columns = [(40,310),(310,460),(460,600),(600,780),(780,970),(970,1160)]
    headings = ['统计类型','场次','胜场','胜率','场均伤害','场均转账']
    def values(name, data):
        return [name, _num(data['count']), _num(data['wins']), percent(data['winrate']), _num(data.get('damage')), _num(data.get('transfer'))]
    sections = [('总体数据', '已采集汇总，非完整累计' if role and observed else '累计档案口径', headings,
                 [values(name,data) for name,data in report.modes])]
    if role:
        info = report.character_info or {}
        sections.append(('角色资料', report.hero, ['项目','数据','项目','数据','项目','数据'], [
            ['角色 ID',str(report.character_id), '角色等级',_num(info.get('level')), '拥有状态',
             '已拥有' if info.get('owned') is True else '未拥有' if info.get('owned') is False else '—'],
            ['累计伤害','—','累计转账','—','统计范围',mode_label]]))
    else:
        sections.append(('角色数据 · '+mode_label+(' · 已采集' if observed else ' · 累计'), f'第 {report.page}/{report.pages} 页 · 共 {len(report.heroes)} 位',
                         ['角色','累计场次','累计胜场','胜率','场均伤害','场均转账'],
                         [values(name,data) for name,data in report.display_heroes]))
    bars = report.heroes[:6] if not role else []
    chart_height = 72 + len(bars)*36 if bars else 0
    height = 396 + sum(48+40+max(1,len(s[3]))*46+22 for s in sections) + chart_height + 88
    img = Image.new('RGB',(1200,height),'#F5F6F8')
    d = ImageDraw.Draw(img)
    d.rectangle((24,24,1176,height-24),fill='white')
    title = '角色'+mode_label+'总览' if role else '个人'+mode_label+'累计总览'
    d.text((40,49),'ASTRAL PARTY  /  '+title,font=F(18,True),fill=MUTED,anchor='lm')
    nick,font = _fit(d,p.get('nick','未知玩家'),1100,43,True)
    d.text((40,96),nick,font=font,fill=INK,anchor='lm')
    meta = f"国服 · UID {p.get('uid','—')} · Lv.{_num(p.get('level'))}"
    if role:
        meta += ' · '+report.hero
    meta,font = _fit(d,meta,1100,22)
    d.text((40,142),meta,font=font,fill=MUTED,anchor='lm')
    d.rectangle((40,174,1160,228),fill='#318B25')
    bar = mode_label+(' · 已采集汇总' if role and observed else ' · 累计')
    d.text((56,201),bar,font=F(27,True),fill='white',anchor='lm')
    d.text((1144,201),'胜率  '+percent(stats['winrate']),font=F(30,True),fill='white',anchor='rm')
    metrics = [('已采集场次' if role and observed else '累计场次',_num(stats['count'])),
               ('样本胜率' if role and observed else '累计胜率',percent(stats['winrate'])),
               ('样本胜场' if role and observed else '累计胜场',_num(stats['wins'])),
               ('场均伤害',_num(stats.get('damage'))),('场均转账',_num(stats.get('transfer')))]
    for i,(label,value) in enumerate(metrics):
        x=40+i*224
        d.rectangle((x,246,x+212,340),fill='#F2F4F6')
        d.text((x+106,268),label,font=F(20,True),fill=MUTED,anchor='mm')
        value,font=_fit(d,value,200,34,True)
        d.text((x+106,309),value,font=font,fill=PURPLE if i==1 else INK,anchor='mm')
        d.line((x,340,x+212,340),fill='#97BDCF',width=2)
    note = (report.character_info or {}).get('mode_note','角色档案累计未按模式拆分。') if role else '账号按所选模式累计；下方角色列表为'+mode_label+('已采集统计。' if observed else '累计统计。')
    if p.get('source') == 'public':
        note = '公开协议未提供累计档案，本图不以最近对局推算累计数据。'
    d.text((40,362),note,font=F(17),fill=MUTED,anchor='lm')
    y=396
    for title,note,labels,rows in sections:
        y=_section(d,y,title,note)
        y=_table_header(d,y,columns,labels)
        for i,values_ in enumerate(rows or [['档案未提供']+['—']*5]):
            y=_row(d,y,columns,values_,i,{3:PURPLE} if title.startswith(('总体','角色数据')) else {})
        y+=22
    if bars:
        y=_section(d,y,'角色出场分布','全模式累计 · 前 6 位')
        maximum=max([data['count'] or 0 for _,data in bars] or [1]) or 1
        for name,data in bars:
            _cell(d,(40,300),y+15,name,left=True,size=20)
            width=720*(data['count'] or 0)/maximum
            d.rectangle((320,y+4,1040,y+26),fill='#F0F2F5')
            if width:
                d.rectangle((320,y+4,320+width,y+26),fill='#82BBD2')
            d.text((1140,y+15),_num(data['count']),font=F(20,True),fill=INK,anchor='rm')
            y+=36
        y+=24
    d.text((40,y+4),'— 表示档案未提供该指标；模式场均仅计算有指标的已采集对局。',font=F(17),fill=MUTED,anchor='lm')
    d.text((40,y+30),'本图不含逐场明细；/atp me recent 保留今日记录视图。',font=F(17),fill=MUTED,anchor='lm')
    return img


def render_report(report):
    if report.cumulative:
        return render_career_report(report)
    """纯展示层：不访问网络，不在绘制时再次计算战绩。"""
    p, summary = report.profile, report.summary
    titles = {'overview': '战绩总览', 'recent': '今日战绩', 'hero': '角色战绩'}
    columns = [(40, 280), (280, 420), (420, 560), (560, 740), (740, 950), (950, 1160)]
    headings = ['统计范围', '场次', '胜场', '胜率', '场均伤害', '场均转账']

    def stats_row(label, stats):
        return [label, _num(stats['count']), _num(stats['wins']), percent(stats['winrate']),
                _num(stats['damage']), _num(stats['transfer'])]

    overall = []
    if report.view == 'overview' and report.battle_type == 'all':
        overall.append(['累计战绩', _num(p.get('total')), _num(p.get('wins')),
                        percent(p.get('winrate')), '—', '—'])
    if report.character_total is not None:
        overall.append(['角色档案·全模式', _num(report.character_total), '—', '—', '—', '—'])
    overall.append(stats_row('筛选样本', summary))
    overall.extend(stats_row(mode, stats) for mode, stats in report.modes)
    sections = [('总体数据' if report.view == 'overview' else '样本统计',
                 '按所选模式及日期汇总', columns, headings, overall, {3: PURPLE, 4: GREEN, 5: GREEN})]
    if report.view != 'recent':
        hero_rows = [stats_row(name, stats) for name, stats in report.heroes[:5]]
        sections.append(('角色数据', '按筛选结果出场数排序 · 最多 5 位', columns,
                         ['角色', '场次', '胜场', '胜率', '场均伤害', '场均转账'],
                         hero_rows, {3: PURPLE, 4: GREEN, 5: GREEN}))
    detail_columns = [(40, 110), (110, 350), (350, 610), (610, 810), (810, 1010), (1010, 1160)]
    details = []
    for i, row in enumerate(report.display_rows, start=(report.page - 1) * report.page_size):
        outcome = '胜' if row['win'] is True else '负' if row['win'] is False else '未知'
        details.append([f'{i+1:02}', row['hero'], row['mode'],
                        _num(row['damage']), _num(row['transfer']), outcome])
    sections.append(('对局明细', f"第 {report.page}/{report.pages} 页 · 共 {summary['count']} 场", detail_columns,
                     ['序号', '角色', '模式', '伤害', '转账', '结果'], details, {}))
    height = 396 + sum(48 + 40 + max(1, len(section[4])) * 46 + 22 for section in sections) + 84
    img = Image.new('RGB', (1200, height), '#F5F6F8')
    d = ImageDraw.Draw(img)
    d.rectangle((24, 24, 1176, height-24), fill='white')
    d.text((40, 49), 'ASTRAL PARTY  /  ' + titles[report.view], font=F(18, True), fill=MUTED, anchor='lm')
    nick, font = _fit(d, p.get('nick', '未知玩家'), 900, 43, True)
    d.text((40, 96), nick, font=font, fill=INK, anchor='lm')
    meta = f"国服 · UID {p.get('uid', '—')} · Lv.{_num(p.get('level'))}"
    meta += ' · 公开查询' if p.get('source') == 'public' else ' · 账号档案'
    if report.hero:
        meta += ' · ' + report.hero
    meta, font = _fit(d, meta, 1100, 21)
    d.text((40, 142), meta, font=font, fill=MUTED, anchor='lm')
    if p.get('online'):
        d.ellipse((1050, 88, 1062, 100), fill=GREEN)
        d.text((1074, 94), '在线', font=F(21, True), fill=GREEN, anchor='lm')
    d.rectangle((40, 174, 1160, 228), fill='#318B25')
    d.text((56, 201), '今日表现' if report.view == 'recent' else report.battle_type.upper() + ' 表现', font=F(27, True), fill='white', anchor='lm')
    description = f"{summary['count']} 场 / {summary['wins']} 胜 {summary['losses']} 负"
    if summary['unknown']:
        description += f" / {summary['unknown']} 场未知"
    d.text((218, 201), description, font=F(21), fill='white', anchor='lm')
    d.text((1144, 201), '胜率  ' + percent(summary['winrate']), font=F(30, True), fill='white', anchor='rm')
    if report.view == 'overview' and report.battle_type == 'all':
        metrics = [('累计场次', _num(p.get('total'))), ('累计胜率', percent(p.get('winrate'))),
                   ('累计胜场', _num(p.get('wins'))), ('获赞', _num(p.get('praise'))),
                   ('拥有角色', _num(p.get('heroes')))]
    else:
        metrics = [('样本场次', str(summary['count'])), ('样本胜率', percent(summary['winrate'])),
                   ('样本胜场', str(summary['wins'])), ('场均伤害', _num(summary['damage'])),
                   ('场均转账', _num(summary['transfer']))]
    for i, (label, value) in enumerate(metrics):
        x = 40 + i * 224
        d.rectangle((x, 246, x+212, 340), fill='#F2F4F6')
        d.text((x+106, 268), label, font=F(20, True), fill=MUTED, anchor='mm')
        value, font = _fit(d, value, 200, 34, True)
        d.text((x+106, 309), value, font=font, fill=PURPLE if i == 1 else INK, anchor='mm')
        d.line((x, 340, x+212, 340), fill='#97BDCF', width=2)
    scope = '范围：' + report.scope
    if report.hero:
        scope += ' · ' + report.hero
    if p.get('stale'):
        scope += ' · 包含缓存数据'
    if p.get('records_error'):
        scope = '近期对局获取失败，请稍后重试；当前仅展示已获取的身份资料。'
    scope, font = _fit(d, scope, 1120, 17)
    d.text((40, 362), scope, font=font, fill=MUTED, anchor='lm')
    y = 396
    for title, note, cols, labels, rows, colors in sections:
        y = _section(d, y, title, note)
        y = _table_header(d, y, cols, labels)
        if not rows:
            rows = [['暂无记录'] + ['—'] * (len(cols)-1)]
        for i, values in enumerate(rows):
            cell_colors = dict(colors)
            if title == '对局明细':
                cell_colors[5] = GREEN if values[5] == '胜' else RED if values[5] == '负' else MUTED
            y = _row(d, y, cols, values, i, cell_colors)
        y += 22
    d.text((40, y+5), f"伤害有效样本 {summary['damage_count']} 场 · 转账有效样本 {summary['transfer_count']} 场；均值不含缺失项。",
           font=F(17), fill=MUTED, anchor='lm')
    d.text((40, y+30), f'仅含已获取/保存的历史，非完整生涯；每页 {report.page_size} 条；{report.missing_time} 条无时间记录已排除。',
           font=F(17), fill=MUTED, anchor='lm')
    return img


def render_stats_card(p, recent):
    return render_report(build_report(p, recent))


def render_report_png(report):
    buf = io.BytesIO()
    render_report(report).save(buf, 'PNG')
    return buf.getvalue()


def render_stats_card_png(p, recent):
    return render_report_png(build_report(p, recent))
