# -*- coding: utf-8 -*-
"""星趴 QQ 机器人核心（多账号版）。
每个 QQ 用户登录自己的游戏账号，凭据按用户隔离存放，互不干扰。"""
import os, json, time, hashlib, threading, random
from pathlib import Path
from functools import wraps
from match_history import MatchHistoryStore, merge_rows

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / 'userdata'
DATA_DIR.mkdir(exist_ok=True)

# ── 设备号持久化：必须在 import astral 之前设置 ──
_dev_file = DATA_DIR / 'device.txt'
if _dev_file.exists():
    _seed = _dev_file.read_text(encoding='utf-8').strip()
else:
    _seed = hashlib.sha256(os.urandom(32)).hexdigest()[:16]
    _dev_file.write_text(_seed, encoding='utf-8')
os.environ['COMPUTERNAME'] = 'astralbot-' + _seed

from astral.client import AstralClient, ProtocolError            # noqa: E402
from astral.sdk_login import (request_code, phone_login,        # noqa: E402
                              token_login, pick,
                              DEFAULT_DEVICE_ID, LoginError)
from astral import client_ver                                     # noqa: E402
from astral.paths import data_file                                # noqa: E402

GAME_HOST, GAME_PORT = '101.132.186.71', 8800

HERO = {}
_f = data_file('character_ids.json')
if _f.exists():
    for h in json.loads(_f.read_text(encoding='utf-8')):
        HERO[int(h['id'])] = h['name']
MODE = {1: '标准对战', 2: '合作挑战', 3: '限时玩法', 4: '合作挑战'}


def _pairs(p, idx):
    try:
        items = p.task.condition1[idx].params
    except Exception:
        return {}
    out = {}
    for x in items:
        a, b = getattr(x, 'param', None), getattr(x, 'param1', None)
        if a is not None and b is not None:
            out[int(b)] = int(a)
    return out


def build_profile(s2c):
    p = s2c.player
    cond = dict(getattr(p.task, 'condition', {}) or {})
    total, wins = int(cond.get(14, 0)), int(cond.get(13, 0))
    sp = getattr(p, 'showPlayer', None)
    praise = int(getattr(sp, 'praiseNum', 0) or cond.get(22, 0))
    my_uid = int(getattr(p, 'id', 0) or 0)

    skins = 0
    for it in (getattr(p, 'bag_items', []) or []):
        s = str(int(getattr(it, 'item_id', 0)))
        if len(s) == 9 and s.startswith('100'):
            skins += 1

    cards = getattr(p, 'roleCard', {}) or {}
    fc, wc = _pairs(p, 17), _pairs(p, 18)
    for hid, c in cards.items():
        n = int(getattr(c, 'fightCount', 0) or 0)
        if n or int(hid) not in fc:
            fc[int(hid)] = n
    usage = sorted(((HERO.get(h, '角色#%d' % h), n) for h, n in fc.items() if n),
                   key=lambda x: -x[1])

    character_stats = {}
    for hid in set(fc) | set(wc) | set(cards):
        card = cards.get(hid)
        character_total = fc.get(hid)
        won = wc.get(hid)  # Missing task entries are unknown, not zero wins.
        strengthen = getattr(card, 'pve_strengthen', None)
        character_stats[str(hid)] = {
            'name': HERO.get(int(hid), '角色#%d' % int(hid)),
            'total': character_total, 'wins': won,
            'level': int(getattr(strengthen, 'level', 0) or getattr(card, 'lv', 0) or 0) if card is not None else None,
            'owned': card is not None}
    mode_counts = {str(k):int(v) for k,v in (getattr(p, 'mapModeCount', {}) or {}).items()}
    mode_wins = {str(k):int(v) for k,v in (getattr(p, 'mapModeWinCount', {}) or {}).items()}

    recent = []
    records = list(getattr(sp, 'record', []) or []) + list(getattr(sp, 'replayRecord', []) or [])
    for r in records:
        rows = list(getattr(r, 'data', []) or [])
        mine = next((it for it in rows if int(getattr(it, 'playerId', 0) or 0) == my_uid), None)
        if mine is None:
            continue
        recent.append({
            'time': int(getattr(r, 'time', 0) or 0),
            'mode_id': int(getattr(r, 'mapType', getattr(mine, 'mapType', 0)) or 0),
            'mode': MODE.get(int(getattr(r, 'mapType', getattr(mine, 'mapType', 0)) or 0), '未知模式'),
            'hero_id': int(getattr(mine, 'heroId', 0) or 0),
            'hero': HERO.get(int(getattr(mine, 'heroId', 0) or 0), '?'),
            'rank': int(getattr(mine, 'rank', 0) or 0),
            'replay': str(getattr(mine, 'replayId', '') or getattr(r, 'replayId', '') or ''),
        })
    recent = merge_rows([], recent)

    return {'nick': getattr(p, 'nick', '?'), 'uid': my_uid,
            'level': int(getattr(p, 'level', 0) or 0),
            'total': total, 'wins': wins,
            'winrate': round(100.0 * wins / total, 1) if total else 0,
            'heroes': len(cards), 'skins': skins, 'praise': praise,
            'character_stats': character_stats, 'mode_counts': mode_counts, 'mode_wins': mode_wins,
            'usage': usage, 'hero_usage': {str(h): n for h, n in fc.items()}, 'recent': recent}


def format_profile(p):
    L = ['【我的信息】%s（UID %d）' % (p['nick'], p['uid']),
         '等级 %d ｜ 场次 %d ｜ 胜场 %d ｜ 胜率 %s%%' % (p['level'], p['total'], p['wins'], p['winrate']),
         '拥有角色 %d ｜ 皮肤 %d ｜ 获赞 %d' % (p['heroes'], p['skins'], p['praise'])]
    if p['usage']:
        L += ['', '角色使用排行：'] + ['%d. %s — %d 场' % (i, nm, n)
                                     for i, (nm, n) in enumerate(p['usage'][:8], 1)]
    if p['recent']:
        L += ['', '最近对局：'] + ['· %s [%s] %s 第%d名' % (
            time.strftime('%m-%d %H:%M', time.localtime(r['time'])),
            r['mode'], r['hero'], r['rank'] or 4) for r in p['recent']]
    return '\n'.join(L)


def synchronized(method):
    """保护账号状态和共享连接；耗时的回放/渲染在锁外执行。"""
    @wraps(method)
    def wrapped(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)
    return wrapped


class AccountManager:
    """多账号管理：每个 QQ 用户一个独立游戏账号。"""

    def __init__(self):
        self._lock = threading.RLock()
        self._history_store = MatchHistoryStore(DATA_DIR / 'match_history')
        self.acct_dir = DATA_DIR / 'accounts'
        self.acct_dir.mkdir(exist_ok=True)
        self.pending_phone = {}     # qq_uid -> 手机号（等验证码中）
        self.sessions = {}          # qq_uid -> {'client': AstralClient, 'profile': dict}
        self.pending_bind = {}      # 绑定码 -> (member_openid, 时间戳)
        self.bind_file = DATA_DIR / 'bindings.json'
        self.bindings = self._load_bindings()
        # 查询专用小号：常驻在线，/查 优先借它（配置 userdata/service.json）
        self.service_file = DATA_DIR / 'service.json'
        self._start_hb()
        threading.Thread(target=self.ensure_service_login, daemon=True).start()

    # ── 每账号存储 ──
    def _dir(self, uid):
        d = self.acct_dir / uid
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _token_file(self, uid):
        return self._dir(uid) / 'token.json'

    def _load_token(self, uid):
        try:
            return json.loads(self._token_file(uid).read_text(encoding='utf-8'))
        except Exception:
            return None

    def _save_token(self, uid, resp, tel=''):
        tok = pick(resp, 'content.data.accessToken', 'content.accessToken',
                   'data.accessToken', 'content.access_token', 'data.access_token',
                   'content.0.accessToken', 'accessToken', 'content.token', 'data.token')
        if not tok:
            return False
        self._token_file(uid).write_text(json.dumps(
            {'accessToken': tok, 'tel': str(tel) if tel else '',
             'saved_at': time.strftime('%Y-%m-%d %H:%M:%S')},
            ensure_ascii=False), encoding='utf-8')
        return True

    # ── 私聊：登录 ──
    @synchronized
    def send_code(self, uid, phone):
        request_code(phone)
        self.pending_phone[uid] = phone
        return '验证码已发送，请回复：/验证码 123456'

    @synchronized
    def login_with_code(self, uid, code):
        phone = self.pending_phone.pop(uid, '')
        if not phone:
            raise LoginError('请先 /登录 手机号')
        sid, resp = phone_login(phone, code)
        self._save_token(uid, resp, phone)
        return '登录成功 ✅\n' + format_profile(self._enter(uid, sid))

    @synchronized
    def get_profile(self, uid):
        """取档案：优先复用活动会话，否则用存着的 accessToken 自动登录。"""
        s = self.sessions.get(uid)
        if s and s.get('client') and s['client'].sock:
            return self._refresh_profile(s), ''
        tok = self._load_token(uid)
        if not tok or not tok.get('accessToken'):
            return None, '还没登录。群里 @我 发 /登录 获取网页登录链接（或私聊 /登录 手机号）。'
        try:
            sid, resp = token_login(tok['accessToken'], tok.get('tel', ''))
            self._save_token(uid, resp, tok.get('tel', ''))
            return self._enter(uid, sid), ''
        except Exception as e:
            return None, '登录态过期了，请重新 /登录 一次。（%s）' % e

    def _refresh_profile(self, session):
        """活动会话每 30 秒刷新一次自己的展示资料，失败时明确返回缓存。"""
        now = time.monotonic()
        if now - session.get('refreshed_at', float('-inf')) < 30:
            return session['profile']
        session['refreshed_at'] = now
        profile = dict(session['profile'])
        try:
            show = session['client'].get_show_player(int(profile['uid'])).showData
            if int(show.player_id) != int(profile['uid']):
                raise ProtocolError('返回的玩家身份不匹配')
            if show.isShowData:
                stats = show.statistics
                total, wins = int(stats.fightCount), int(stats.winFightCount)
                profile.update(total=total, wins=wins,
                               winrate=round(100 * wins / total, 1) if total else 0,
                               heroes=int(stats.roleCardCount), skins=int(stats.skinCount),
                               praise=int(show.praiseNum))
            if show.isShowFight:
                records = list(show.record) + list(show.replayRecord)
                records.sort(key=lambda r: -int(r.time))
                profile['recent'] = [
                    {'time': int(r.time), 'mode_id': int(r.mapType), 'mode': MODE.get(int(r.mapType), '未知模式'),
                     'hero_id': int(r.heroId), 'hero': HERO.get(int(r.heroId), '未知角色'), 'rank': int(r.rank),
                     'replay': str(r.replayId)} for r in records]
            profile['stale'] = not (show.isShowData and show.isShowFight)
        except Exception as error:
            profile['stale'] = True
            print('[stats] 档案刷新失败，使用缓存:', type(error).__name__)
        profile['recent'] = merge_rows([], profile.get('recent', []))
        profile = self._remember_profile(profile)
        session['profile'] = profile
        return profile

    def _remember_profile(self, profile):
        store = getattr(self, '_history_store', None)
        if store is None:
            return profile
        try:
            return store.merge_profile(profile)
        except (OSError, ValueError, TypeError) as error:
            print('[history] 保存/读取对局历史失败:', type(error).__name__)
            return dict(profile, history_error=True)

    def _enter(self, uid, sid):
        with self._lock:
            c = AstralClient(GAME_HOST, GAME_PORT, timeout=20.0)
            c.connect()
            s2c = None
            for attempt in range(3):
                try:
                    s2c = c.login_china(sid, device_id=DEFAULT_DEVICE_ID, extra='bn')
                    break
                except ProtocolError as e:
                    msg = str(e)
                    if '10012' in msg:
                        c2, _ver, s2c2 = self._probe_ver(uid, sid)
                        if c2 is None:
                            raise ProtocolError('客户端版本不匹配，自动探测失败，请更新 sign.py 的 GAME_VERSION')
                        c.close()
                        c, s2c = c2, s2c2
                        break
                    if '10020' not in msg:
                        raise
                    if attempt >= 2:
                        raise ProtocolError('账号正在别处在线（游戏开着？），关掉后再试')
                    time.sleep(1.5 + attempt)
                    c.close()
                    c = AstralClient(GAME_HOST, GAME_PORT, timeout=20.0)
                    c.connect()
            old = self.sessions.get(uid)
            if old and old.get('client'):
                try:
                    old['client'].close()
                except Exception:
                    pass
            self.sessions[uid] = {'client': c, 'profile': self._remember_profile(build_profile(s2c)), 'refreshed_at': time.monotonic()}
            prof = self.sessions[uid]['profile']
            self._log_sessions()      # 上线即留痕：journalctl 可查，不落盘
            return prof

    def _probe_ver(self, uid, sid):
        def make_session():
            tok = self._load_token(uid) or {}
            if tok.get('accessToken'):
                try:
                    s2, _ = token_login(tok['accessToken'], tok.get('tel', ''))
                    return s2
                except Exception:
                    pass
            return sid
        return client_ver.probe(GAME_HOST, GAME_PORT, make_session,
                                base=client_ver.resolve('cn'), kind='cn',
                                extra='bn', gap=2.0, stop_on_timeout=False)

    # ── 群绑定：把群身份关联到私聊账号 ──
    def _load_bindings(self):
        try:
            return json.loads(self.bind_file.read_text(encoding='utf-8'))
        except Exception:
            return {}

    def _save_bindings(self):
        self.bind_file.write_text(json.dumps(self.bindings, ensure_ascii=False),
                                  encoding='utf-8')

    @synchronized
    def make_bind_code(self, member_openid):
        code = '%06d' % random.randint(0, 999999)
        self.pending_bind[code] = (member_openid, time.time())
        return code

    @synchronized
    def bind(self, user_openid, code):
        item = self.pending_bind.pop(code, None)
        if not item:
            return '绑定码不对或已过期，请在群里重新 /绑定'
        member_openid, ts = item
        if time.time() - ts > 600:
            return '绑定码已过期（10分钟有效），请在群里重新 /绑定'
        self.bindings[member_openid] = user_openid
        self._save_bindings()
        return '绑定成功 ✅ 以后在群里 @我 发 /我的 就能查你的战绩'

    @synchronized
    def resolve_member(self, member_openid):
        return self.bindings.get(member_openid)

    @synchronized
    def unbind_member(self, member_openid):
        """解除群身份绑定（/解绑）：删除 member->user 映射；user 名下的票保留，可重新绑定找回。"""
        if member_openid not in self.bindings:
            return False
        self.bindings.pop(member_openid, None)
        self._save_bindings()
        return True

    # ── 战绩历史存档（/我的 卡片"变化"对比用）────────────────
    def load_stats_history(self, uid, limit=20):
        p = DATA_DIR / 'stats_history' / ('%s.json' % uid)
        try:
            items = json.loads(p.read_text(encoding='utf-8'))
            return items[-limit:]
        except Exception:
            return []

    def save_stats_snapshot(self, uid, prof):
        try:
            d = DATA_DIR / 'stats_history'
            d.mkdir(parents=True, exist_ok=True)
            p = d / ('%s.json' % uid)
            items = []
            try:
                items = json.loads(p.read_text(encoding='utf-8'))
            except Exception:
                pass
            items.append({'ts': int(time.time()),
                          'total': int((prof or {}).get('total', 0) or 0),
                          'wins': int((prof or {}).get('wins', 0) or 0),
                          'praise': int((prof or {}).get('praise', 0) or 0)})
            p.write_text(json.dumps(items[-30:], ensure_ascii=False), encoding='utf-8')
        except Exception as e:
            print('[history] 存档失败:', e)

    def _log_sessions(self):
        """往 stdout/journal 打当前在线清单（不存文件，仅限服务器本地看）"""
        try:
            su = self.service_uid()
            print('[sessions] 在线游戏账号 %d 个' % len(self.sessions))
            for uid, s in self.sessions.items():
                prof = (s or {}).get('profile') or {}
                tag = ' [服务小号]' if su and uid == su else ''
                print('[sessions] · %s（UID %s）%s' % (prof.get('nick', '?'), prof.get('uid', 0), tag))
        except Exception:
            pass

    # ── 查询专用小号（常驻，供 /查 借用）──────────────────
    def service_uid(self):
        try:
            return json.loads(self.service_file.read_text(encoding='utf-8')).get('user_openid', '')
        except Exception:
            return ''

    @synchronized
    def ensure_service_login(self):
        """把小号挂上。只动小号本人——顶号风险只影响小号，它就是为此准备的。"""
        su = self.service_uid()
        if not su:
            return
        s = self.sessions.get(su)
        if s and s.get('client'):
            return
        if not self._load_token(su):
            return                      # 从没登录过，等首次 /登录
        try:
            self.get_profile(su)
        except Exception:
            pass

    @synchronized
    def list_sessions(self):
        out = []
        for uid, s in self.sessions.items():
            prof = (s or {}).get('profile') or {}
            if s and s.get('client'):
                out.append({'openid': uid, 'nick': prof.get('nick', '?'),
                            'uid': prof.get('uid', 0)})
        return out

    # ── 心跳保活：不发 5003，服务器会静默丢弃该会话的所有请求 ──
    def _start_hb(self):
        def loop():
            n = 0
            while True:
                time.sleep(10)        # 官方客户端约每 5 秒心跳一次；30 秒太慢会被服务器掐
                n += 1
                # 心跳无需等待响应，不占用业务锁，避免慢登录导致其他账号掉线。
                for uid_, s in self.sessions.copy().items():
                    c = (s or {}).get('client')
                    if c:
                        try:
                            c.heartbeat()
                        except Exception as e:
                            print('[sessions] 心跳失败，剔除死会话:', type(e).__name__)
                            c.close()
                            # 后台线程只标记连接关闭；业务线程在同一把锁下移除。
                if n % 30 == 0:
                    with self._lock:
                        for uid_, s in self.sessions.copy().items():
                            if not getattr(s.get('client'), 'sock', None):
                                self.sessions.pop(uid_, None)
                if n % 30 == 0:          # 约每 5 分钟自愈一次小号登录
                    self.ensure_service_login()
        threading.Thread(target=loop, daemon=True).start()
