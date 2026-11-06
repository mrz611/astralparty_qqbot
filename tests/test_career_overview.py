"""Career totals must use archive counters, never a ten-match sample."""
import unittest
from unittest.mock import Mock, patch
from astral.proto_loader import new_msg
from bot_core import build_profile
from stats_report import build_report, format_report
from stats_query import StatsQuery


def login_fixture():
    response = new_msg('protocol.ConnectS2C')
    p=response.player
    p.id=1234567; p.nick='累计档案测试'; p.level=22
    p.task.condition[14]=2000; p.task.condition[13]=1000
    p.mapModeCount[1]=600; p.mapModeCount[2]=1300; p.mapModeCount[4]=100
    p.mapModeWinCount[1]=200; p.mapModeWinCount[2]=740; p.mapModeWinCount[4]=60
    p.roleCard[117].fightCount=48
    p.roleCard[117].pve_strengthen.level=6
    p.roleCard[102].fightCount=60
    p.task.condition1[17].params.add(param=40,param1=117)
    p.task.condition1[18].params.add(param=32,param1=117)
    p.task.condition1[18].params.add(param=25,param1=102)
    for i in range(10):
        record=p.showPlayer.record.add()
        record.time=1700000000+i
        record.replayId=f'{i+1:016d}'
        me=record.data.add()
        me.playerId=p.id; me.heroId=102; me.rank=1; me.mapType=2
    return response


class CareerOverviewTests(unittest.TestCase):
    def setUp(self):
        self.profile=build_profile(login_fixture())

    def test_adapter_preserves_character_counts_wins_and_mode_counts(self):
        hero=self.profile['character_stats']['117']
        self.assertEqual(hero['total'],48)
        self.assertEqual(hero['wins'],32)
        self.assertEqual(hero['level'],6)
        self.assertEqual(self.profile['mode_counts']['2'],1300)
        self.assertEqual(self.profile['mode_wins']['2'],740)

    def test_jasmine_not_in_recent_ten_still_has_career_data(self):
        self.assertFalse(any(r['hero_id']==117 for r in self.profile['recent']))
        for query in ['茉莉','117']:
            report=build_report(self.profile,self.profile['recent'],'hero',query,battle_type='all')
            self.assertEqual(report.summary['count'],48)
            self.assertEqual(report.summary['wins'],32)
            self.assertEqual(report.summary['winrate'],66.7)
            self.assertTrue(report.cumulative)
            self.assertEqual(report.rows,[])
            self.assertNotIn('对局明细',format_report(report))

    def test_account_pve_totals_do_not_equal_ten_match_sample(self):
        report=build_report(self.profile,self.profile['recent'])
        self.assertEqual(report.summary['count'],1400)
        self.assertEqual(report.summary['wins'],800)
        self.assertEqual(report.summary['winrate'],57.1)
        self.assertEqual(report.rows,[])
        self.assertEqual(report.sample_count,0)

    def test_all_mode_career_matches_protocol_totals(self):
        report=build_report(self.profile,self.profile['recent'],battle_type='all')
        self.assertEqual(report.summary['count'],2000)
        self.assertEqual(report.summary['wins'],1000)

    def test_unavailable_role_mode_split_is_not_inferred_from_recent(self):
        report=build_report(self.profile,self.profile['recent'],'hero','117')
        self.assertIn('已采集',report.scope)
        self.assertEqual(dict(report.modes)['PVE·已采集']['count'],0)
        self.assertEqual(report.character_info['mode_source'],'observed')
        self.assertIsNone(report.summary['damage'])
        self.assertIsNone(report.summary['transfer'])

    def test_existing_cached_usage_is_a_valid_fallback(self):
        profile={'usage':[('茉莉',48)],'uid':1234567}
        report=build_report(profile,[],'hero','117',battle_type='all')
        self.assertEqual(report.summary['count'],48)
        self.assertIsNone(report.summary['wins'])

    def test_missing_career_counters_are_unknown_not_zero(self):
        report=build_report({'uid':1234567},[],'hero','117',battle_type='all')
        self.assertIsNone(report.summary['count'])
        self.assertIsNone(report.summary['wins'])
        self.assertIn('累计场次 —',format_report(report))

    def test_career_query_does_not_fetch_replays(self):
        with patch('bot_ext.replay_rows') as replay, patch('bot_viz.render_report_png',return_value=b'png'):
            result=StatsQuery(Mock(),images=True).present(self.profile,'hero','117',battle_type='all')
        replay.assert_not_called()
        self.assertEqual(result.image,b'png')
        self.assertIn('累计场次 48',result.text)

    def test_more_than_ten_characters_are_paginated_as_aggregates(self):
        characters={str(i):{'name':f'角色{i}','total':i,'wins':1} for i in range(101,126)}
        report=build_report({'character_stats':characters},[],page=2)
        self.assertEqual(len(report.heroes),25)
        self.assertEqual(len(report.display_heroes),5)
        self.assertEqual(report.pages,2)
        self.assertEqual(report.rows,[])

    def test_career_renderer_has_aggregate_sections_without_match_details(self):
        import bot_viz
        if not bot_viz._FP_REG or not bot_viz._FP_BOLD:
            self.skipTest('CJK font unavailable')
        report=build_report(self.profile,self.profile['recent'],'hero','117')
        with patch.object(bot_viz,'_section',wraps=bot_viz._section) as sections:
            image=bot_viz.render_report(report)
        titles=[call.args[2] for call in sections.call_args_list]
        self.assertIn('总体数据',titles)
        self.assertIn('角色资料',titles)
        self.assertNotIn('对局明细',titles)
        self.assertLess(image.height,1100)

class RolePveBreakdownTests(unittest.TestCase):
    def test_only_pve_account_proves_role_cumulative_pve(self):
        profile={'total':200,'wins':120,'mode_counts':{'2':150,'4':50},
                 'character_stats':{'117':{'name':'茉莉','total':48,'wins':32}}}
        report=build_report(profile,[],'hero','117',battle_type='all')
        modes=dict(report.modes)
        self.assertEqual(modes['PVE·累计']['count'],48)
        self.assertEqual(modes['PVE·累计']['wins'],32)
        self.assertEqual(modes['PVP·累计']['count'],0)
        self.assertEqual(report.character_info['mode_source'],'account_single_family')

    def test_mixed_account_only_uses_observed_role_games(self):
        profile={'total':200,'mode_counts':{'1':100,'2':100},
                 'character_stats':{'117':{'name':'茉莉','total':48,'wins':32}},'recent':[
            {'hero_id':117,'hero':'茉莉','mode_id':2,'rank':1,'replay':'1234567890123456','damage':80},
            {'hero_id':117,'hero':'茉莉','mode_id':4,'rank':2,'replay':'1234567890123457','damage':120},
            {'hero_id':117,'hero':'茉莉','mode_id':1,'rank':1,'replay':'1234567890123458'},
            {'hero_id':102,'hero':'芬妮','mode_id':2,'rank':1,'replay':'1234567890123459'}]}
        report=build_report(profile,profile['recent'],'hero','117')
        modes=dict(report.modes)
        self.assertEqual(report.summary['count'],2)
        self.assertEqual(report.character_info['total'],48)
        self.assertEqual(modes['PVE·已采集']['count'],2)
        self.assertEqual(modes['PVE·已采集']['wins'],1)
        self.assertEqual(modes['PVE·已采集']['damage'],100)
        self.assertNotIn('PVP·已采集',modes)
        self.assertIn('不等于完整',report.character_info['mode_note'])
        self.assertEqual(report.rows,[])

    def test_account_mode_gap_does_not_prove_all_pve(self):
        profile={'total':200,'mode_counts':{'2':100},'hero_usage':{'117':48}}
        report=build_report(profile,[],'hero','117')
        self.assertEqual(report.character_info['mode_source'],'observed')
        self.assertNotIn('PVE·累计',dict(report.modes))

    def test_missing_mode_does_not_count_as_pve(self):
        profile={'hero_usage':{'117':48},'recent':[{'hero_id':117,'hero':'茉莉','rank':1}]}
        report=build_report(profile,profile['recent'],'hero','117')
        self.assertEqual(dict(report.modes)['PVE·已采集']['count'],0)

class DefaultPveModeTests(unittest.TestCase):
    def setUp(self):
        self.profile={'total':200,'mode_counts':{'1':100,'2':100},
            'character_stats':{'117':{'name':'茉莉','total':48,'wins':32}},'recent':[
             {'hero_id':117,'hero':'茉莉','mode_id':2,'rank':1,'replay':'1234567890123456'},
             {'hero_id':117,'hero':'茉莉','mode_id':4,'rank':2,'replay':'1234567890123457'},
             {'hero_id':117,'hero':'茉莉','mode_id':1,'rank':1,'replay':'1234567890123458'}]}

    def test_character_defaults_to_pve_without_pvp_rows(self):
        report=build_report(self.profile,self.profile['recent'],'hero','茉莉')
        self.assertEqual(report.summary['count'],2)
        self.assertEqual(report.summary['wins'],1)
        self.assertTrue(all(name.startswith('PVE') for name,_ in report.modes))
        self.assertIn('角色 · PVE',report.scope)
        self.assertNotIn('PVP·已采集',format_report(report))

    def test_explicit_pvp_selects_pvp_metrics(self):
        report=build_report(self.profile,self.profile['recent'],'hero','茉莉',battle_type='pvp')
        self.assertEqual(report.summary['count'],1)
        self.assertEqual(report.summary['wins'],1)
        self.assertTrue(all(name.startswith('PVP') for name,_ in report.modes))
        self.assertIn('角色 · PVP',report.scope)

    def test_account_role_list_and_mode_rows_follow_default_pve(self):
        report=build_report(self.profile,self.profile['recent'])
        self.assertEqual([name for name,_ in report.modes],['PVE'])
        self.assertEqual(report.heroes[0][1]['count'],2)
        explicit=build_report(self.profile,self.profile['recent'],battle_type='pvp')
        self.assertEqual([name for name,_ in explicit.modes],['PVP'])
        self.assertEqual(explicit.heroes[0][1]['count'],1)
