"""Statistics semantics and query routing, without external services."""
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from stats_report import build_sample_report as build_report, format_report
from stats_query import StatsQuery
import bot_ext
from test_bot import bot


class ReportTests(unittest.TestCase):
    def setUp(self):
        self.profile = {'uid': 1234567, 'nick': '测试玩家', 'total': 100,
                        'wins': 40, 'winrate': 40., 'recent': [
            {'hero': '芬妮', 'rank': 1, 'mode': '标准对战', 'damage': 0, 'transfer': 10},
            {'hero': '芬妮', 'rank': 2, 'mode': '标准对战', 'damage': 100},
            {'hero': '帕露南', 'rank': 0, 'mode': '合作挑战'}]}

    def test_unknown_results_and_missing_metrics_are_not_losses_or_zeros(self):
        report = build_report(self.profile, self.profile['recent'], battle_type='all')
        self.assertEqual(report.summary['wins'], 1)
        self.assertEqual(report.summary['losses'], 1)
        self.assertEqual(report.summary['unknown'], 1)
        self.assertEqual(report.summary['winrate'], 50.)
        self.assertEqual(report.summary['damage'], 50)
        self.assertEqual(report.summary['transfer'], 10)
        self.assertEqual(report.summary['damage_count'], 2)
        self.assertIn('结果未知', format_report(report))

    def test_hero_filter_and_mode_totals_use_same_sample(self):
        report = build_report(self.profile, self.profile['recent'], 'hero', '芬妮', battle_type='all')
        self.assertEqual(report.sample_count, 3)
        self.assertEqual(report.summary['count'], 2)
        self.assertEqual(sum(s['count'] for _, s in report.modes), 2)
        self.assertEqual(report.heroes[0][0], '芬妮')
        self.assertEqual(self.profile['recent'][0].get('win'), None)

    def test_character_after_tenth_record_is_not_discarded(self):
        rows = [{'hero': '芬妮', 'rank': 1}] * 10 + [{'hero': '帕露南', 'rank': 1}]
        report = build_report(self.profile, rows, 'hero', '帕露南', battle_type='all')
        self.assertEqual(report.summary['count'], 1)
        self.assertIn('帕露南', format_report(report))

    def test_empty_report_has_no_fabricated_winrate(self):
        report = build_report({}, [])
        self.assertIsNone(report.summary['winrate'])
        self.assertIsNone(report.summary['damage'])

    def test_public_adapter_keeps_unavailable_career_statistics_missing(self):
        result = {'info': SimpleNamespace(name='公开玩家', lv=20),
                  'records': [SimpleNamespace(heroId=101, rank=0, mapType=2)]}
        profile = bot_ext.public_profile('1234567', result)
        self.assertIsNone(profile['total'])
        self.assertNotIn('replay', profile['recent'][0])
        report = build_report(profile, profile['recent'])
        self.assertIsNone(report.summary['winrate'])
        self.assertIn('累计数据未提供', format_report(report))

    def test_text_only_does_not_fetch_replays(self):
        manager = Mock()
        manager.get_profile.return_value = (self.profile, '')
        with patch.object(bot_ext, 'replay_rows') as fetch:
            result = StatsQuery(manager, images=False).mine('alice')
        fetch.assert_not_called()
        self.assertIsNone(result.image)
        self.assertIn('测试玩家', result.text)

    def test_image_failure_preserves_same_statistics_in_fallback(self):
        with patch.object(bot_ext, 'replay_rows', return_value=self.profile['recent']), \
                patch('bot_viz.render_report_png', side_effect=RuntimeError('font')):
            result = StatsQuery(Mock(), images=True).present(self.profile, battle_type='all')
        self.assertIsNone(result.image)
        self.assertIn('累计场次 100', result.text)
        self.assertNotIn('场均伤害 50', result.text)

    def test_public_query_uses_same_report_and_renderer(self):
        with patch.object(bot_ext, 'query_uid', return_value={
                'ok': True, 'info': SimpleNamespace(name='公开玩家', lv=20), 'records': []}), \
                patch('bot_viz.render_report_png', return_value=b'png') as render:
            result = StatsQuery(Mock(), images=True).player('1234567', 'alice')
        self.assertEqual(result.image, b'png')
        self.assertEqual(render.call_args.args[0].profile['source'], 'public')
        self.assertIn('累计数据未提供', result.text)

    def test_query_error_does_not_render(self):
        with patch.object(bot_ext, 'query_uid', return_value={'ok': False, 'msg': '未找到'}):
            result = StatsQuery(Mock(), images=True).player('1234567')
        self.assertEqual(result.text, '未找到')
        self.assertIsNone(result.image)


class CommandTests(unittest.TestCase):
    def test_private_recent_routes_with_identity(self):
        with patch.object(bot, 'profile_of', return_value='ok') as query:
            self.assertEqual(bot.handle_private('/我的 近期', 'alice'), 'ok')
        query.assert_called_once_with('alice', 'recent', '', 'pve')

    def test_group_public_hero_does_not_require_binding(self):
        with patch.object(bot, 'mgr') as manager, patch.object(bot, 'do_query', return_value='ok') as query:
            manager.resolve_member.return_value = None
            self.assertEqual(bot.handle_group('/查 1234567 角色 芬妮', 'member'), 'ok')
        query.assert_called_once_with('1234567', 'member', 'hero', '芬妮', 'pve')

    def test_unbound_group_self_query_uses_member_identity(self):
        # 网页登录模型：未绑定群友用群身份直接查（票存于 member_openid 槽位）
        with patch.object(bot, 'mgr') as manager, patch.object(bot, 'profile_of', return_value='ok') as query:
            manager.resolve_member.return_value = None
            self.assertEqual(bot.handle_group('/我的 近期', 'member'), 'ok')
        query.assert_called_once_with('member', 'recent', '', 'pve')

    def test_group_unbind_removes_binding(self):
        with patch.object(bot, 'mgr') as manager:
            manager.unbind_member.return_value = True
            self.assertIn('已解绑', bot.handle_group('/解绑', 'member'))
        manager.unbind_member.assert_called_once_with('member')

    def test_group_unbind_without_binding(self):
        with patch.object(bot, 'mgr') as manager:
            manager.unbind_member.return_value = False
            self.assertIn('无需解绑', bot.handle_group('/解绑', 'member'))

    def test_invalid_selector_provides_usage(self):
        for text in ['/查', '/查 abc', '/查 1234567 角色', '/我的 其他']:
            with self.subTest(text=text):
                self.assertIn('用法', bot.stats_command(text, 'alice'))

class RefreshTests(unittest.TestCase):
    def test_refresh_updates_live_profile_and_throttles_requests(self):
        import bot_core
        client = Mock()
        client.get_show_player.return_value.showData = SimpleNamespace(
            player_id=1234567, isShowData=True, isShowFight=True,
            statistics=SimpleNamespace(fightCount=110, winFightCount=45,
                                       roleCardCount=20, skinCount=8), praiseNum=15,
            record=[SimpleNamespace(time=100, heroId=101, rank=1, mapType=1, replayId='1234567890123456')],
            replayRecord=[])
        manager = bot_core.AccountManager.__new__(bot_core.AccountManager)
        session = {'client': client, 'profile': {'uid': 1234567, 'total': 100}}
        profile = manager._refresh_profile(session)
        self.assertEqual(profile['total'], 110)
        self.assertEqual(profile['recent'][0]['rank'], 1)
        self.assertFalse(profile['stale'])
        manager._refresh_profile(session)
        self.assertEqual(client.get_show_player.call_count, 1)

    def test_refresh_failure_keeps_cache_and_marks_it(self):
        import bot_core
        client = Mock()
        client.get_show_player.side_effect = TimeoutError()
        manager = bot_core.AccountManager.__new__(bot_core.AccountManager)
        profile = manager._refresh_profile({'client': client, 'profile': {'uid': 1234567, 'total': 100}})
        self.assertEqual(profile['total'], 100)
        self.assertTrue(profile['stale'])

class ATPTests(unittest.TestCase):
    def test_default_command_queries_pve(self):
        with patch.object(bot, 'profile_of', return_value='ok') as query:
            self.assertEqual(bot.handle_private('/atp me', 'alice'), 'ok')
        query.assert_called_once_with('alice', 'overview', '', 'pve')

    def test_recent_defaults_to_pve_and_modes_can_precede_or_follow_view(self):
        for text, mode in [('/atp me recent', 'pve'), ('/atp me pvp recent', 'pvp'),
                           ('/atp me recent all', 'all')]:
            with self.subTest(text=text), patch.object(bot, 'profile_of', return_value='ok') as query:
                bot.handle_private(text, 'alice')
                query.assert_called_once_with('alice', 'recent', '', mode)

    def test_hero_and_public_uid_commands(self):
        with patch.object(bot, 'do_query', return_value='ok') as query:
            bot.stats_command('/atp 1234567 pvp char 芬妮', None)
        query.assert_called_once_with('1234567', None, 'hero', '芬妮', 'pvp')

    def test_help_and_invalid_combinations(self):
        self.assertIn('默认 PVE', bot.stats_command('/atp help', None))
        for command in ['/atp wrong', '/atp me pve pvp', '/atp me recent recent', '/atp me char']:
            self.assertIn('用法', bot.stats_command(command, 'alice'))

    def test_both_known_pve_ids_are_included_and_unknown_modes_excluded(self):
        rows = [{'hero': '芬妮', 'rank': 1, 'mode_id': i} for i in [1, 2, 3, 4, 0]]
        report = build_report({}, rows)
        self.assertEqual([r['mode_id'] for r in report.rows], [2, 4])
        self.assertEqual(build_report({}, rows, battle_type='pvp').summary['count'], 1)
        self.assertEqual(build_report({}, rows, battle_type='all').summary['count'], 5)

    def test_today_uses_beijing_midnight_even_when_now_is_utc(self):
        from datetime import datetime, timezone
        from stats_report import BEIJING
        now = datetime(2026, 11, 5, 3, tzinfo=timezone.utc)
        start = int(datetime(2026, 11, 5, tzinfo=BEIJING).timestamp())
        rows = [{'hero': '芬妮', 'mode_id': 2, 'rank': 1, 'time': t}
                for t in [start-1, start, start+60, start+86400]]
        rows += [{'hero': '芬妮', 'mode_id': 1, 'rank': 1, 'time': start+60},
                 {'hero': '芬妮', 'mode_id': 4, 'rank': 1}]
        report = build_report({}, rows, 'recent', now=now)
        self.assertEqual([r['time'] for r in report.rows], [start, start+60])
        self.assertEqual(report.missing_time, 1)
        self.assertEqual(report.date_label, '2026-11-05')
        self.assertIn('UTC+8', report.scope)
        self.assertIn('不保证覆盖当日全部', format_report(report))

    def test_today_with_only_undated_rows_reports_uncertainty(self):
        report = build_report({}, [{'hero': '芬妮', 'mode_id': 2, 'rank': 1}], 'recent')
        self.assertEqual(report.missing_time, 1)
        self.assertEqual(report.summary['count'], 0)
        self.assertIn('缺少时间', format_report(report))

    def test_public_today_is_rejected_before_network(self):
        with patch.object(bot_ext, 'query_uid') as fetch:
            result = StatsQuery(Mock()).player('1234567', view='recent')
        fetch.assert_not_called()
        self.assertIn('没有对局时间', result.text)

    def test_mode_and_today_filters_run_before_replay_fetch(self):
        from datetime import datetime
        from stats_report import BEIJING
        today = int(datetime.now(BEIJING).replace(hour=1, minute=0, second=0, microsecond=0).timestamp())
        profile = {'uid': 1234567, 'recent': [
            {'hero':'芬妮','rank':1,'mode_id':2,'time':today},
            {'hero':'芬妮','rank':1,'mode_id':1,'time':today},
            {'hero':'芬妮','rank':1,'mode_id':2,'time':today-86400}]}
        with patch.object(bot_ext, 'replay_rows', side_effect=lambda p: p['recent']) as replay, \
                patch('bot_viz.render_report_png', return_value=b'png') as render:
            StatsQuery(Mock(), images=True).present(profile, 'recent')
        self.assertEqual(len(replay.call_args.args[0]['recent']), 1)
        report = render.call_args.args[0]
        self.assertEqual(report.sample_count, 3)
        self.assertEqual(report.summary['count'], 1)

    def test_default_pve_text_does_not_expose_all_mode_career_as_pve(self):
        profile = {'uid': 1234567, 'total': 999, 'wins': 666, 'winrate': 66.6}
        self.assertNotIn('累计 999', format_report(build_report(profile, [])))
        self.assertIn('累计 999', format_report(build_report(profile, [], battle_type='all')))


class CharCommandTests(unittest.TestCase):
    def test_char_selects_self_character_without_changing_mode(self):
        with patch.object(bot, 'profile_of', return_value='ok') as query:
            self.assertEqual(bot.handle_private('/atp me char 芬妮', 'alice'), 'ok')
        query.assert_called_once_with('alice', 'hero', '芬妮', 'pve')

    def test_old_english_keyword_is_replaced_and_chinese_is_unchanged(self):
        self.assertIn('用法', bot.stats_command('/atp me hero 芬妮', 'alice'))
        with patch.object(bot, 'profile_of', return_value='ok') as query:
            bot.handle_private('/我的 角色 芬妮', 'alice')
        query.assert_called_once_with('alice', 'hero', '芬妮', 'pve')
