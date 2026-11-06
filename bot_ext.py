# -*- coding: utf-8 -*-
"""扩展功能：/查 UID 代查公开档案、/回放 回放号免登录战报。
回放统计逻辑【运行时从 main_ref.py 提取】（原项目 main.py 改名放进来）：
用正则抠出 COND_NAME / _rv / _pb / _int / _str / _w / _pad /
fetch_replay_stats 后 exec 执行，保证与 AstralParty_Dex 原版 100% 一致。
游戏更新导致解析变更时，只需替换 main_ref.py。"""
import json
import re
import time
import threading
import urllib.request
from pathlib import Path
from functools import wraps

from bot_core import HERO, MODE, format_profile

ROOT = Path(__file__).resolve().parent
MAIN_REF = ROOT / 'main_ref.py'

# 与原项目一致，机器人固定国服
REPLAY_HOSTS = {'cn': 'https://sereplaycn.feimogames.com/prod/%s',
                'intl': 'https://sereplayjp.feimogames.com/prod/%s'}
REPLAY_REGION = 'cn'
REPLAY_DIR = ROOT / 'userdata' / 'replays'


def replay_url(rid):
    return REPLAY_HOSTS.get(REPLAY_REGION, REPLAY_HOSTS['cn']) % rid


def replay_key(rid):
    return ('intl_%s' % rid) if REPLAY_REGION == 'intl' else str(rid)


# -- 从 main_ref.py 提取原版代码并执行 ------------------------------
def _load_original():
    if not MAIN_REF.exists():
        raise RuntimeError('缺少 main_ref.py -- 把原项目 main.py 改名后放到 astral-bot/ 目录')
    src = MAIN_REF.read_text(encoding='utf-8')
    pats = [
        r"^COND_NAME = \{.*?^COND_NAME\[27\] = [^\n]*",
        r"^def _rv\(d, i\):.*?(?=^def fetch_replay_stats)",
        r"^def fetch_replay_stats\(replay_id\):.*?(?=^\S)",
    ]
    parts = []
    for p in pats:
        m = re.search(p, src, re.M | re.S)
        if not m:
            raise RuntimeError('main_ref.py 版式变了，提取失败（%s），请更新 main_ref.py' % p[:30])
        parts.append(m.group(0))
    ns = {'urllib': urllib, 'REPLAY_DIR': REPLAY_DIR,
          'replay_url': replay_url, 'replay_key': replay_key}
    exec(compile('\n\n'.join(parts), '<main_ref>', 'exec'), ns)
    return ns


_NS = None
_original_lock = threading.Lock()
_replay_locks = [threading.Lock() for _ in range(64)]


def original():
    global _NS
    with _original_lock:
        if _NS is None:
            _NS = _load_original()
    return _NS


def fetch_replay_stats(rid):
    # 固定数量分片锁，避免同一回放同时下载/写缓存，也避免锁表无限增长。
    with _replay_locks[hash(str(rid)) % len(_replay_locks)]:
        return original()['fetch_replay_stats'](rid)


# -- /查：借任一已登录会话代查公开档案 -------------------------------
def manager_locked(method):
    @wraps(method)
    def wrapped(mgr, *args, **kwargs):
        with mgr._lock:
            return method(mgr, *args, **kwargs)
    return wrapped


def any_session(mgr, prefer_uid=None):
    """挑一个在线游戏会话：优先查询专用小号，其次 prefer_uid 本人。"""
    get_su = getattr(mgr, 'service_uid', None)
    su = get_su() if get_su else ''
    if su:
        s = mgr.sessions.get(su)
        if s and s.get('client') and s['client'].sock:
            return su, s
    if prefer_uid:
        s = mgr.sessions.get(prefer_uid)
        if s and s.get('client') and s['client'].sock:
            return prefer_uid, s
    for uid, s in mgr.sessions.items():
        if s and s.get('client') and s['client'].sock:
            return uid, s
    return None, None


def _drop_dead_session(mgr, owner):
    """剔除失效会话（Broken pipe 等），保活线程/自愈会负责重登。"""
    try:
        s = mgr.sessions.pop(owner, None)
        c = (s or {}).get('client')
        if c:
            c.close()
    except Exception:
        pass


@manager_locked
def query_uid(mgr, uid, prefer_uid=None):
    """好友查询路径：5263 GetPlayerSimple(FriendInfo) + 5155 GetPlayerFightRecord。
    只用基础协议，不碰 5153 个人信息全量查询；借任一在线会话（优先服务号），查的人无需登录。"""
    try:
        uid_i = int(uid)                         # protobuf 整数字段不接受 str
    except ValueError:
        return {'ok': False, 'msg': 'UID 必须是纯数字。'}
    tried = set()
    last = ''
    for _round in range(2):                      # 最多换 2 个会话
        owner, s = any_session(mgr, prefer_uid)
        if s is None or owner in tried:
            break
        tried.add(owner)
        c = s['client']                          # 共用的会话连接，查完不能 close
        try:
            ps = c.get_player_simple(uid_i, timeout=15.0)
            info = ps.PlayerInfo
        except Exception as e:
            last = str(e)
            _drop_dead_session(mgr, owner)       # 连接已死：剔除，等保活/自愈重登
            time.sleep(1.2)
            continue
        if not int(getattr(info, 'playerId', 0) or 0):
            return {'ok': False, 'msg': '这个 UID 在国服务器查不到 —— 注意别和 16 位回放号输混。'}
        records_error = False
        try:
            fr = c.get_fight_records(uid_i, index=0, timeout=15.0)
            recs = list(getattr(fr, 'recordData', []) or [])
        except Exception:
            recs = []
            records_error = True
        return {'ok': True, 'info': info, 'records': recs, 'records_error': records_error}
    if tried and last:
        return {'ok': False, 'msg': '查询失败：' + last}
    return {'ok': False, 'msg': ('当前没有可用的查询会话（等服务号上线后即可免登录查询）。\n'
                                '急需可查让任意群友先私聊 /登录 一次。')}


def format_uid_profile(uid, result):
    """兼容旧调用方，文字统计仍走统一报表。"""
    from stats_report import build_report, format_report
    if not result.get('ok'):
        return result.get('msg') or '查询失败'
    profile = public_profile(uid, result)
    return format_report(build_report(profile, profile['recent']))


# -- /回放：免登录，直连回放服务器 -----------------------------------
# ── 观战手牌：官方观战协议（5191 加入 / 5195 退出，仅 PVE 有观战码）──
CARD_NAME = {}
try:
    _cards = json.loads((ROOT / 'assets' / 'data' / 'cards.json').read_text(encoding='utf-8'))
    CARD_NAME = {int(c['id']): c['name'] for c in _cards}
except Exception:
    pass


def _room_hands(res):
    """从 WatchJoinRoomS2C 提取四人手牌（按座位排序）。"""
    out = []
    for p in (getattr(res, 'players', None) or []):
        if getattr(p, 'isBot', False):
            continue
        hero = getattr(p, 'hero', None)
        hid = int(getattr(hero, 'hero_id', 0) or 0) if hero is not None else 0
        cards = []
        if hero is not None:
            for cd in (getattr(hero, 'cards', None) or []):
                cid = int(getattr(cd, 'cardId', 0) or 0)
                cards.append(CARD_NAME.get(cid, '卡#%d' % cid))
        out.append({'nick': str(getattr(p, 'nick', '') or '?'),
                    'slot': int(getattr(p, 'slot', 0) or 0),
                    'hero': HERO.get(hid, '?'),
                    'gold': int(getattr(hero, 'gold', 0) or 0) if hero is not None else 0,
                    'offline': bool(getattr(p, 'off_line', False)),
                    'cards': cards})
    out.sort(key=lambda x: x['slot'])
    return out


@manager_locked
def watch_hands(mgr, watch_code):
    """观战号加房间拿手牌快照即退出。借在线会话（优先 service.json 指定的观战号）。"""
    owner, s = any_session(mgr, None)
    if s is None:
        return {'ok': False, 'msg': ('观战号当前不在线：等几分钟它会自动上线；'
                                    '或先私聊 /登录 一次。')}
    c = s['client']                          # 共用连接，不能 close
    try:
        res = c.watch_join_room(watch_code, timeout=15.0)
    except Exception as e:
        _drop_dead_session(mgr, owner)       # 连接死了就剔除，等保活/自愈
        return {'ok': False, 'msg': '加入观战失败：%s' % e}
    room_id = int(getattr(res, 'room_id', 0) or 0)
    if not room_id:
        try:
            c.watch_exit_room(timeout=5.0)
        except Exception:
            pass
        return {'ok': False, 'msg': ('进不了这个观战码对应的房间：观战码无效/已过期，'
                                    '或该局不是 PVE（只有 PVE 有观战码）。')}
    data = _room_hands(res)
    n_join = len(data)
    # 5191 快照的 players 可能不全：用 5193 拉全量房间状态，谁多信谁
    try:
        full = c.watch_refresh_room_state(
            room_id, int(getattr(res, 'room_server_id', 0) or 0), timeout=15.0)
        more = _room_hands(getattr(full, 'room', None) or full)
        if len(more) > len(data):
            data = more
    except Exception as e:
        print('[watch] 刷新房间状态失败（用加入快照）:', e)
    print('[watch] 房间 #%d 玩家数: 加入快照=%d 最终=%d' % (room_id, n_join, len(data)))
    try:
        c.watch_exit_room(timeout=5.0)
    except Exception:
        pass
    return {'ok': True, 'room_id': room_id, 'players': data}


def public_profile(uid, result):
    """公开协议仅提供身份和近期名次；不将未提供的累计指标伪装为零。"""
    info = result['info']
    recent = []
    for r in list(result.get('records') or []):
        recent.append({'hero_id': int(getattr(r, 'heroId', 0) or 0),
                       'hero': HERO.get(int(getattr(r, 'heroId', 0) or 0), '未知角色'),
                       'rank': int(getattr(r, 'rank', 0) or 0),
                       'mode_id': int(getattr(r, 'mapType', 0) or 0),
                       'mode': MODE.get(int(getattr(r, 'mapType', 0) or 0), '未知模式')})
    return {'nick': getattr(info, 'name', '') or '未知昵称', 'uid': int(uid),
            'level': int(getattr(info, 'lv', 0) or 0),
            'online': bool(getattr(info, 'isOnline', False)),
            'total': None, 'wins': None, 'winrate': None, 'praise': None,
            'heroes': None, 'recent': recent, 'source': 'public',
            'records_error': bool(result.get('records_error'))}


def replay_rows(profile):
    """补齐有回放号的行；回放异常只影响该行，原始档案不变。"""
    from concurrent.futures import ThreadPoolExecutor
    from stats_report import normalize_rows
    rows = normalize_rows(profile.get('recent', []))
    uid = str(profile.get('uid', ''))

    def fill(row):
        rid = str(row.get('replay') or '').strip()
        if re.fullmatch(r'[0-9]{16}', rid):
            try:
                stats = (fetch_replay_stats(rid).get(uid) or {}).get('stats') or {}
                row['damage'], row['transfer'] = stats.get('伤害'), stats.get('转账')
            except Exception as error:
                print('[stats] 回放解析失败:', type(error).__name__)
        return row

    with ThreadPoolExecutor(max_workers=5) as pool:
        return list(pool.map(fill, rows))


def card_from_profile(profile):
    from stats_report import build_report
    from bot_viz import render_report_png
    return render_report_png(build_report(profile, replay_rows(profile)))


def format_watch_hands(res):
    if not res.get('ok'):
        return res.get('msg') or '观战失败'
    L = ['【实时手牌】房间 #%d' % res['room_id']]
    if not res['players']:
        L.append('（没有取到玩家数据）')
    for p in res['players']:
        tag = '（已离线）' if p['offline'] else ''
        hand = '、'.join(p['cards']) if p['cards'] else '（无手牌）'
        L.append('- %s｜%s｜星币 %d%s' % (p['nick'], p['hero'], p['gold'], tag))
        L.append('  手牌：%s' % hand)
    return '\n'.join(L)


STAT_COLS = ['击杀', '伤害', '承伤', '治疗', '星币', '转账']


def format_replay_stats(rid):
    try:
        st = fetch_replay_stats(rid)
    except Exception as e:
        return '回放解析失败：%s' % str(e)[:120]
    if not st:
        return '取不到这个回放 -- 回放号可能输错或已过期（国服回放号，别拿海外服的号来查）。'
    cols = [c for c in STAT_COLS if any(v['stats'].get(c) for v in st.values())]
    L = ['【回放战报】%s' % rid, '']
    for pid, v in st.items():
        hero = HERO.get(v['heroId'], '角色#%s' % v['heroId'])
        parts = ['%s · %s' % (v['name'] or pid, hero)]
        parts += ['%s %s' % (c, v['stats'].get(c, 0)) for c in cols if v['stats'].get(c)]
        if v.get('gold'):
            parts.append('剩余星币 %s' % v['gold'])
        L.append('｜'.join(parts))
    L.append('')
    L.append('逐轮复盘（每回合手牌/筹码）后续版本推出。')
    return '\n'.join(L)
