# -*- coding: utf-8 -*-
"""QQ 接入层。私聊登录，群聊查战绩；/回放 回放号免登录战报。"""
import os, re
import asyncio
import threading
import uuid
from dataclasses import dataclass

try:
    import qqbot
except ImportError:
    import botpy as qqbot

from bot_core import AccountManager
import bot_ext
import bot_weblogin
from stats_query import StatsQuery

APP_ID = os.environ.get('QQ_APPID', '')
SECRET = os.environ.get('QQ_SECRET', '')

HELP_TEXT = (
    '【星趴战绩机器人】\n'
    '星引擎 Party（吉星派对）战绩查询。\n'
    '\n'
    '登录系（需要你的游戏账号）\n'
    '· /登录 — 首次使用：群里 @我 发 /登录，点链接在网页完成手机号+验证码\n'
    '· /我的 — 我的近10场PVE战绩卡片\n'
    '· /解绑 — 解除当前绑定关系\n'
    '· /atp help — 更多战绩查询指令\n'
    '\n'
    '查询系（无需登录）\n'
    '· /查 游戏UID — 查任意玩家公开战绩\n'
    '· /回放 16位回放号 — 单局四人详细战报\n'
    '· /手牌 观战码 — PVE 对局实时手牌快照\n'
    '\n'
    '⚠️⚠️ 顶号警告 ⚠️⚠️\n'
    '/我的 会登录你的游戏账号——查询瞬间\n'
    '如果你正在游戏里，会被当场踢下线！\n'
    '千万不要边玩边查，打完这局再查！'
)

mgr = AccountManager()


CARD_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'cards')
CARD_BASE = os.environ.get('CARD_BASE_URL', '')   # 开源版：必填，例 http://你的服务器IP:8420
CARD_PORT = int(os.environ.get('CARD_PORT', '8420'))


def _start_card_server():
    """内置迷你 HTTP 服务：把生成的卡片 PNG 暴露成公网 URL 供 QQ 平台抓图"""
    import threading
    import functools
    from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
    os.makedirs(CARD_DIR, exist_ok=True)
    handler = bot_weblogin.make_handler(CARD_DIR, mgr)

    def run():
        try:
            httpd = ThreadingHTTPServer(('0.0.0.0', CARD_PORT), handler)
            print('[cards] 卡片图片服务已启动 :%d（目录 %s）' % (CARD_PORT, CARD_DIR))
            httpd.serve_forever()
        except Exception as e:
            print('[cards] 图片服务启动失败（将回退纯文字）:', e)

    threading.Thread(target=run, daemon=True).start()


_card_lock = threading.Lock()


@dataclass(frozen=True)
class Reply:
    text: str
    image: bytes | None = None


def save_card(png: bytes) -> str:
    """唯一文件名与串行清理，避免并发请求覆盖图片。"""
    with _card_lock:
        os.makedirs(CARD_DIR, exist_ok=True)
        name = 'card_' + uuid.uuid4().hex + '.png'
        path = os.path.join(CARD_DIR, name)
        with open(path, 'wb') as f:
            f.write(png)
        files = sorted((os.path.join(CARD_DIR, n) for n in os.listdir(CARD_DIR)
                        if n.startswith('card_') and n.endswith('.png')),
                       key=os.path.getmtime)
        for old in files[:-50]:
            if old != path:
                try:
                    os.remove(old)
                except OSError:
                    pass
    return CARD_BASE.rstrip('/') + '/' + name


def profile_of(qq_uid, view='overview', hero='', battle_type='pve', page=1, usage_card=False):
    query = StatsQuery(mgr, images=bool(CARD_BASE))
    query.usage_card = usage_card   # 中文 /我的 专用：角色使用排行卡；/atp me 保持原卡
    result = query.mine(qq_uid, view, hero, battle_type, page)
    return Reply(result.text, result.image)


def do_query(arg, requester=None, view='overview', hero='', battle_type='pve', page=1):
    if not re.fullmatch(r'[0-9]{5,10}', arg):
        return '用法：/查 游戏UID [近期 或 角色 角色名]'
    try:
        result = StatsQuery(mgr, images=bool(CARD_BASE)).player(arg, requester, view, hero, battle_type, page)
        return Reply(result.text, result.image)
    except Exception as error:
        print('[stats] 查询失败:', type(error).__name__)
        return '查询失败，请稍后重试。'


ATP_HELP = (
    '【ATP 战绩查询】默认 PVE\n'
    '/atp me — 账号 PVE 累计总览\n'
    '/atp me recent — 今日 PVE（北京时间）\n'
    '/atp me char 芬妮 — 角色 PVE 汇总\n'
    '/atp me pvp — PVP 累计；/atp me all — 全模式累计\n'
    '/atp me pvp recent — 今日 PVP\n'
    '/atp me pvp char 芬妮 — 角色 PVP 汇总\n'
    '/atp 1234567 — 公开玩家 PVE 战绩\n'
    '/atp 1234567 char 芬妮 — 公开协议无累计档案时显示缺失\n'
    '· 角色支持中文名 / ID / 英文名：char 芬妮 / char 102 / char Fanny\n'
    '· 翻页：/atp me page 2 或 /atp me recent page 2（每页20条）\n'
    '总览读取累计档案；recent 使用今日可用记录。\n'
    '公开 UID 查询没有对局时间，不支持 recent。'
)


def stats_command(text, requester):
    """/atp <me|UID> [pve|pvp|all] [recent|char 角色名]，兼容中文入口。"""
    tokens = text.split()
    if not tokens or tokens[0] not in ('/atp', '/我的', '我的', '/查'):
        return None
    if tokens[0] == '/atp':
        if len(tokens) < 2 or tokens[1].lower() in ('help', '帮助'):
            return ATP_HELP
        target, options = tokens[1], tokens[2:]
        mine = target.lower() == 'me'
    else:
        mine = tokens[0] != '/查'
        if not mine and len(tokens) < 2:
            return '用法：' + ATP_HELP
        target = 'me' if mine else tokens[1]
        options = tokens[1:] if mine else tokens[2:]
    if not mine and not re.fullmatch(r'[0-9]{5,10}', target):
        return '用法：' + ATP_HELP
    view, hero, battle_type, page = 'overview', '', 'pve', 1
    selected_mode = selected_page = False
    reserved = {'pve', 'pvp', 'all', 'recent', '近期', '今日', 'char', '角色', 'page', '页'}
    while options:
        token, options = options[0].lower(), options[1:]
        if token in ('pve', 'pvp', 'all') and not selected_mode:
            battle_type, selected_mode = token, True
        elif token in ('recent', '近期', '今日') and view == 'overview':
            view = 'recent'
        elif token in ('page', '页') and not selected_page and options and options[0].isascii() and options[0].isdigit():
            if len(options[0]) > 6 or int(options[0]) < 1:
                return '页码必须为正整数。'
            page, selected_page, options = int(options[0]), True, options[1:]
        elif token in ('char', '角色') and view == 'overview' and options:
            names = []
            while options and options[0].lower() not in reserved:
                names.append(options.pop(0))
            if not names:
                return '用法：' + ATP_HELP
            hero, view = ' '.join(names), 'hero'
        else:
            return '用法：' + ATP_HELP
    page_args = {'page': page} if selected_page else {}
    if mine:
        if not requester:
            return '还没绑定。先在群里 /绑定，然后私聊完成绑定。'
        return profile_of(requester, view, hero, battle_type,
                          usage_card=(tokens[0] in ('/我的', '我的') and view == 'overview'),
                          **page_args)
    return do_query(target, requester, view, hero, battle_type, **page_args)


def do_watch(arg):
    code = arg.strip()
    if not code:
        return '用法：/手牌 观战码（游戏内 PVE 房间界面的观战码）'
    try:
        res = bot_ext.watch_hands(mgr, code)
        return bot_ext.format_watch_hands(res)
    except Exception as e:
        return '观战出错：%s' % e


def do_replay(arg):
    m = re.match(r'^(\d{16})$', arg)
    if not m:
        return '用法：/回放 16位回放号（从游戏内对局记录复制）'
    return bot_ext.format_replay_stats(m.group(1))


def handle_private(text, user_openid):
    """私聊指令（user_openid 标识身份）"""
    text = text.strip()
    if text in ('/帮助', '帮助', '/help', 'help'):
        return HELP_TEXT
    stats = stats_command(text, user_openid)
    if stats is not None:
        return stats
    m = re.match(r'^/登录\s+(\d{11})$', text)
    if m:
        try:
            return mgr.send_code(user_openid, m.group(1))
        except Exception as e:
            return '发送验证码失败：%s' % e
    m = re.match(r'^/验证码\s+(\d{4,8})$', text)
    if m:
        try:
            return mgr.login_with_code(user_openid, m.group(1))
        except Exception as e:
            return '登录失败：%s' % e
    m = re.match(r'^/绑定\s+(\d{6})$', text)
    if m:
        return mgr.bind(user_openid, m.group(1))
    m = re.match(r'^/回放\s+(\S+)$', text)
    if m:
        return do_replay(m.group(1))
    m = re.match(r'^/手牌\s+(\S+)$', text)
    if m:
        return do_watch(m.group(1))
    return HELP_TEXT


def handle_group(text, member_openid):
    """群聊指令（member_openid 标识身份，需先绑定）"""
    text = text.strip()
    if text in ('/帮助', '帮助', '/help', 'help'):
        return HELP_TEXT
    if text in ('/绑定', '绑定'):
        code = mgr.make_bind_code(member_openid)
        return '你的绑定码：%s\n私聊我发：/绑定 %s（10分钟内有效）' % (code, code)
    if text in ('/解绑', '解绑'):
        if mgr.unbind_member(member_openid):
            return '已解绑 ✅ 之后 /我的 将使用你本人在本群网页登录（/登录）的账号。'
        return '你当前没有绑定关系，无需解绑。'
    if text in ('/登录', '登录'):
        if not CARD_BASE:
            return '网页登录未配置（缺 CARD_BASE_URL），请联系管理员。'
        token = bot_weblogin.issue_token(member_openid)
        return '点下面链接登录你的游戏账号（10分钟有效，勿转发他人）：\n%s/login?t=%s' % (CARD_BASE.rstrip('/'), token)
    if re.match(r'^/登录\s+\d', text):
        return '为保护隐私，请勿在群里发手机号！请发 /登录 获取网页登录链接。'
    stats = stats_command(text, mgr.resolve_member(member_openid) or member_openid)
    if stats is not None:
        return stats
    m = re.match(r'^/回放\s+(\S+)$', text)
    if m:
        return do_replay(m.group(1))
    m = re.match(r'^/手牌\s+(\S+)$', text)
    if m:
        return do_watch(m.group(1))
    return HELP_TEXT


class MyClient(qqbot.Client):
    async def _dispatch(self, handler, *args):
        # 不排无限队列：所有 worker 忙时立即回复，避免积压过期的被动回复。
        active = getattr(self, '_active_commands', 0)
        if active >= 4:
            return Reply('当前查询较多，请稍后再试。')
        self._active_commands = active + 1
        task = asyncio.create_task(asyncio.to_thread(handler, *args))

        def finished(done):
            self._active_commands -= 1
            if not done.cancelled():
                done.exception()  # 回调取消后仍消费后台异常。

        task.add_done_callback(finished)
        try:
            result = await asyncio.shield(task)
            return result if isinstance(result, Reply) else Reply(result)
        except Exception as e:
            print('[command error]', type(e).__name__)
            return Reply('处理失败，请稍后重试。')

    async def on_ready(self):
        print('[bot] 已上线，等待消息...')

    async def _reply(self, message, target, result, group=False):
        text = result.text[:1900]
        try:
            if result.image and CARD_BASE:
                try:
                    url = await asyncio.to_thread(save_card, result.image)
                    if group:
                        media = await self.api.post_group_file(
                            group_openid=target, file_type=1, url=url, srv_send_msg=False)
                        await self.api.post_group_message(
                            group_openid=target, msg_type=7, media=media,
                            msg_id=message.id, msg_seq=1)
                    else:
                        media = await self.api.post_c2c_file(
                            openid=target, file_type=1, url=url, srv_send_msg=False)
                        await self.api.post_c2c_message(
                            openid=target, msg_type=7, media=media,
                            msg_id=message.id, msg_seq=1)
                    return
                except Exception as e:
                    print('[reply error] 图片发送失败，回退纯文字:', type(e).__name__)
            await message.reply(content=text)
        except Exception as e:
            print('[reply error]', type(e).__name__)

    async def on_group_at_message_create(self, message):
        uid = str(getattr(message.author, 'member_openid', '') or '')
        gid = str(getattr(message, 'group_openid', '') or '')
        if not uid or not gid:
            return
        print('[group] 收到消息')
        text = re.sub(r'<@!\d+>', '', message.content).strip()
        result = await self._dispatch(handle_group, text, uid)
        await self._reply(message, gid, result, group=True)

    async def on_c2c_message_create(self, message):
        uid = str(getattr(message.author, 'user_openid', '') or '')
        if not uid:
            return
        print('[c2c] 收到消息')
        result = await self._dispatch(handle_private, message.content.strip(), uid)
        await self._reply(message, uid, result)


if __name__ == '__main__':
    if not APP_ID or not SECRET:
        raise SystemExit('请设置 QQ_APPID 和 QQ_SECRET')
    _start_card_server()
    intents = qqbot.Intents(public_messages=True)
    client = MyClient(intents=intents)
    client.run(appid=APP_ID, secret=SECRET)
