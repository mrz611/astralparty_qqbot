"""Regression coverage for character resolution, retained match history and pagination."""
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch, Mock
from concurrent.futures import ThreadPoolExecutor
from characters import CHARACTERS
from match_history import MatchHistoryStore
from stats_report import build_sample_report as build_report, format_report
from stats_query import StatsQuery
import bot_core
import bot_ext
from test_bot import bot


def rows(start, end, hero_id=102):
    return [{'replay': f'{i:016d}', 'time': 1700000000+i, 'hero_id':hero_id,
             'hero': '芬妮' if hero_id == 102 else '帕露南',
             'rank':1, 'mode_id':2, 'mode':'合作挑战'} for i in range(start, end)]


class CharacterTests(unittest.TestCase):
    def test_name_id_english_and_fullwidth_name_find_same_character(self):
        for query in ['芬妮', '102', '１０２', 'Fanny', ' fAnNy ']:
            with self.subTest(query=query):
                report = build_report({}, rows(1, 3), 'hero', query)
                self.assertEqual(report.hero, '芬妮')
                self.assertEqual(report.summary['count'], 2)

    def test_stable_id_survives_display_name_change(self):
        record = dict(rows(1, 2)[0], hero='显示名变化')
        self.assertEqual(build_report({}, [record], 'hero', '102').summary['count'], 1)

    def test_unknown_character_produces_validation_message(self):
        result = StatsQuery(Mock()).present({'recent':rows(1, 3)}, 'hero', '不存在的角色')
        self.assertIn('未识别角色', result.text)

    def test_valid_character_with_no_sample_does_not_claim_no_career(self):
        report = build_report({}, rows(1, 3, 101), 'hero', '102')
        self.assertIn('不代表该角色从未出场', format_report(report))


class PagingTests(unittest.TestCase):
    def test_more_than_ten_are_summarized_and_displayed(self):
        report = build_report({}, rows(1, 36))
        self.assertEqual(report.summary['count'], 35)
        self.assertEqual(len(report.display_rows), 20)
        self.assertEqual(report.pages, 2)
        second = build_report({}, rows(1, 36), page=2)
        self.assertEqual(len(second.display_rows), 15)
        self.assertEqual(second.summary['count'], 35)
        self.assertIn('第 2/2 页', format_report(second))
        self.assertNotIn('0000000000000001', format_report(second))

    def test_character_filter_before_pagination(self):
        data = rows(1, 31, 101) + rows(31, 58)
        report = build_report({}, data, 'hero', '102', page=2)
        self.assertEqual(report.summary['count'], 27)
        self.assertEqual(len(report.display_rows), 7)

    def test_service_enriches_only_visible_page_but_preserves_total(self):
        def enrich(profile):
            return [dict(row, damage=100, transfer=20) for row in profile['recent']]
        with patch.object(bot_ext, 'replay_rows', side_effect=enrich) as fetch, \
                patch('bot_viz.render_report_png', return_value=b'png') as render:
            from datetime import datetime
            from stats_report import BEIJING
            start = int(datetime.now(BEIJING).replace(hour=0,minute=0,second=0,microsecond=0).timestamp())
            data = [dict(row,time=start+i*60) for i,row in enumerate(rows(1,46))]
            result = StatsQuery(Mock(), images=True).present({'recent':data}, view='recent', page=2)
        self.assertEqual(len(fetch.call_args.args[0]['recent']), 20)
        report = render.call_args.args[0]
        self.assertEqual(report.summary['count'], 45)
        self.assertEqual(report.sample_count, 45)
        self.assertEqual(report.page, 2)
        self.assertEqual(report.pages, 3)
        self.assertEqual(result.image, b'png')

    def test_invalid_page_returns_message(self):
        result = StatsQuery(Mock()).present({'recent':rows(1, 4)}, page=2)
        self.assertIn('页码超出', result.text)

    def test_command_char_and_page_do_not_consume_each_other(self):
        for command in ['/atp me char 102 page 2', '/atp me page 2 char 芬妮']:
            with self.subTest(command=command), patch.object(bot, 'profile_of', return_value='ok') as query:
                bot.stats_command(command, 'alice')
                self.assertEqual(query.call_args.kwargs['page'], 2)
                self.assertEqual(query.call_args.args[1], 'hero')

    def test_paging_preserves_web_login_member_fallback(self):
        with patch.object(bot, 'mgr') as manager, patch.object(bot, 'profile_of', return_value='ok') as query:
            manager.resolve_member.return_value = None
            bot.handle_group('/atp me char 102 page 2', 'member')
        query.assert_called_once_with('member', 'hero', '102', 'pve', page=2)

    def test_public_adapter_does_not_truncate_supplied_rows(self):
        data = [SimpleNamespace(heroId=102, rank=1, mapType=2) for _ in range(25)]
        profile = bot_ext.public_profile('1234567', {'info':SimpleNamespace(name='玩家', lv=20), 'records':data})
        self.assertEqual(len(profile['recent']), 25)
        self.assertEqual(profile['recent'][0]['hero_id'], 102)

    def test_login_adapter_does_not_truncate_supplied_rows(self):
        records = [SimpleNamespace(time=r['time'], replayId=r['replay'], data=[
            SimpleNamespace(playerId=1234567, heroId=102, rank=1, mapType=2)]) for r in rows(1, 26)]
        player = SimpleNamespace(id=1234567, task=SimpleNamespace(condition={}),
                                 showPlayer=SimpleNamespace(record=records), roleCard={})
        profile = bot_core.build_profile(SimpleNamespace(player=player))
        self.assertEqual(len(profile['recent']), 25)
        self.assertEqual(profile['recent'][0]['hero_id'], 102)


class HistoryTests(unittest.TestCase):
    def test_overlapping_snapshots_accumulate_after_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            store = MatchHistoryStore(directory)
            store.merge_profile({'uid':1234567, 'recent':rows(1, 11)})
            profile = MatchHistoryStore(directory).merge_profile({'uid':1234567, 'recent':rows(6, 16)})
            self.assertEqual(len(profile['recent']), 15)
            self.assertEqual(profile['recent'][0]['replay'], '0000000000000015')
            self.assertEqual(profile['history_source'], 'local')

    def test_accounts_are_isolated(self):
        with tempfile.TemporaryDirectory() as directory:
            store = MatchHistoryStore(directory)
            store.merge_profile({'uid':1111111, 'recent':rows(1, 11)})
            profile = store.merge_profile({'uid':2222222, 'recent':rows(20, 25)})
            self.assertEqual(len(profile['recent']), 5)

    def test_later_replay_id_upgrades_undated_identity_without_duplicate(self):
        with tempfile.TemporaryDirectory() as directory:
            store = MatchHistoryStore(directory)
            first = dict(rows(1, 2)[0], replay='', damage=0)
            store.merge_profile({'uid':1234567, 'recent':[first]})
            result = store.merge_profile({'uid':1234567, 'recent':rows(1, 2)})
            self.assertEqual(len(result['recent']), 1)
            self.assertEqual(result['recent'][0]['damage'], 0)
            self.assertTrue(result['recent'][0]['replay'])

    def test_corrupt_history_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'cn_1234567.json'
            path.write_text('broken', encoding='utf-8')
            with self.assertRaises(ValueError):
                MatchHistoryStore(directory).merge_profile({'uid':1234567,'recent':rows(1, 2)})
            self.assertEqual(path.read_text(encoding='utf-8'), 'broken')

    def test_unidentified_rows_are_not_duplicated_across_snapshots(self):
        with tempfile.TemporaryDirectory() as directory:
            store = MatchHistoryStore(directory)
            row = {'hero':'芬妮','mode_id':2,'rank':1}
            store.merge_profile({'uid':1234567,'recent':[row]})
            result = store.merge_profile({'uid':1234567,'recent':[row]})
            self.assertEqual(len(result['recent']), 1)

    def test_parallel_merges_keep_all_observed_records(self):
        with tempfile.TemporaryDirectory() as directory:
            store = MatchHistoryStore(directory)
            with ThreadPoolExecutor(max_workers=4) as pool:
                list(pool.map(lambda i: store.merge_profile({'uid':1234567,'recent':rows(i*10+1,i*10+11)}), range(4)))
            result = store.merge_profile({'uid':1234567,'recent':[]})
            self.assertEqual(len(result['recent']), 40)

    def test_public_profiles_do_not_write_own_history(self):
        with tempfile.TemporaryDirectory() as directory:
            profile = {'uid':1234567,'recent':rows(1, 4),'source':'public'}
            self.assertIs(MatchHistoryStore(directory).merge_profile(profile), profile)
            self.assertEqual(list(Path(directory).iterdir()), [])

class AdditionalHistoryTests(unittest.TestCase):
    def test_explicit_retention_keeps_latest_records(self):
        with tempfile.TemporaryDirectory() as directory:
            profile = MatchHistoryStore(directory, limit=3).merge_profile({'uid':1234567,'recent':rows(1, 8)})
            self.assertEqual([r['replay'] for r in profile['recent']], [f'{i:016d}' for i in (7,6,5)])
            self.assertEqual(profile['history_limit'], 3)

    def test_career_character_usage_does_not_depend_on_recent_sample(self):
        report = build_report({'hero_usage':{'102':48}}, rows(1, 3, 101), 'hero', '102')
        self.assertEqual(report.summary['count'], 0)
        self.assertEqual(report.character_total, 48)
        self.assertIn('未分模式', format_report(report))

    def test_login_combines_record_and_replay_record_without_duplicates(self):
        data = []
        for row in rows(1, 16):
            data.append(SimpleNamespace(time=row['time'], replayId=row['replay'], data=[
                SimpleNamespace(playerId=1234567,heroId=102,rank=1,mapType=2)]))
        player = SimpleNamespace(id=1234567, task=SimpleNamespace(condition={}),
                                 showPlayer=SimpleNamespace(record=data[:10],replayRecord=data[5:]),roleCard={})
        profile = bot_core.build_profile(SimpleNamespace(player=player))
        self.assertEqual(len(profile['recent']), 15)
    def test_new_runtime_character_excluded_by_mode_still_formats_empty_report(self):
        report = build_report({}, [{'hero_id':999,'hero':'新角色','mode_id':1,'rank':1}], 'hero', '999')
        self.assertEqual(report.hero, '新角色')
        self.assertIsNone(report.character_total)
        self.assertIn('暂无符合条件', format_report(report))
