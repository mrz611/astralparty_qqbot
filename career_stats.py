"""Cumulative account/character data only; never derive career totals from recent matches."""
from characters import CHARACTERS


def counters(count=None, wins=None):
    rate = round(100 * wins / count, 1) if count and wins is not None and 0 <= wins <= count else (0.0 if count == 0 and wins == 0 else None)
    return {'count':count, 'wins':wins, 'losses':count-wins if count is not None and wins is not None and count >= wins else None,
            'winrate':rate, 'damage':None, 'transfer':None, 'damage_count':0, 'transfer_count':0, 'unknown':0}


def account_counters(profile, battle_type):
    if battle_type == 'all':
        return counters(profile.get('total'), profile.get('wins'))
    counts, wins = profile.get('mode_counts') or {}, profile.get('mode_wins') or {}
    known = bool(counts) or profile.get('total') == 0
    ids = (2,4) if battle_type == 'pve' else (1,)
    count = sum(int(counts.get(str(i), counts.get(i, 0))) for i in ids) if known else None
    known_wins = bool(wins) or profile.get('wins') == 0
    won = sum(int(wins.get(str(i), wins.get(i, 0))) for i in ids) if known and known_wins else None
    return counters(count, won)


def character_entries(profile):
    result = {}
    for key, data in (profile.get('character_stats') or {}).items():
        ident = int(key)
        result[ident] = dict(data, id=ident, name=data.get('name') or CHARACTERS.names.get(ident, f'角色#{ident}'))
    # Compatibility with existing cached profiles before the new adapter is loaded.
    for key, value in (profile.get('hero_usage') or {}).items():
        ident = int(key)
        result.setdefault(ident, {'id':ident,'name':CHARACTERS.names.get(ident, f'角色#{ident}'), 'total':value})
    for name, count in profile.get('usage', []):
        try:
            ident, canonical = CHARACTERS.resolve(name)
        except ValueError:
            continue
        result.setdefault(ident, {'id':ident, 'name':canonical, 'total':count})
    return result


def character_mode_data(profile, ident, name, archive):
    """A complete role split is only inferred when the entire account has one mode family."""
    raw_counts = profile.get('mode_counts') or {}
    counts = {int(key):int(value) for key,value in raw_counts.items()}
    total = profile.get('total')
    role_total = archive['count']
    coherent = (isinstance(total,int) and total >= 0 and bool(counts)
                and all(value >= 0 for value in counts.values()) and sum(counts.values()) == total
                and isinstance(role_total,int) and 0 <= role_total <= total)
    only_pve = coherent and all(key in (2,4) or value == 0 for key,value in counts.items())
    only_pvp = coherent and all(key == 1 or value == 0 for key,value in counts.items())
    if only_pve or only_pvp:
        return [('PVE·累计',archive if only_pve else counters(0,0)),
                ('PVP·累计',archive if only_pvp else counters(0,0))], {
                    'mode_source':'account_single_family',
                    'mode_note':'账号累计仅含 '+('PVE' if only_pve else 'PVP')+'，角色模式归属可确认。'}
    from stats_report import normalize_rows, summarize
    from match_history import merge_rows
    observed = normalize_rows(merge_rows([], profile.get('recent') or []))
    selected = [row for row in observed if CHARACTERS.matches(row,ident,name)]
    pve = [row for row in selected if row.get('mode_id') in (2,4)
           or (row.get('mode_id') is None and row['mode']=='合作挑战')]
    pvp = [row for row in selected if row.get('mode_id')==1
           or (row.get('mode_id') is None and row['mode']=='标准对战')]
    return [('PVE·已采集',summarize(pve)), ('PVP·已采集',summarize(pvp))], {
        'mode_source':'observed', 'mode_observed_count':len(selected),
        'mode_note':'PVE / PVP 仅汇总已采集对局，不等于完整角色累计。'}


def selected_character_mode(profile, ident, name, archive, battle_type):
    split, metadata = character_mode_data(profile,ident,name,archive)
    if battle_type == 'all':
        return archive, [('角色累计·全模式',archive)] + split, metadata
    chosen = next((label,stats) for label,stats in split if label.startswith(battle_type.upper()))
    if metadata['mode_source']=='observed':
        metadata = dict(metadata,mode_note=battle_type.upper()+' 仅汇总已采集对局，不等于完整角色累计。')
    return chosen[1], [chosen], metadata


def career_data(profile, view, hero, battle_type):
    entries = character_entries(profile)
    if view == 'hero':
        candidates = [{'hero_id':ident,'hero':data['name']} for ident,data in entries.items()]
        ident, name = CHARACTERS.resolve(hero, candidates)
        data = dict(entries.get(ident, {}))
        stats = counters(data.get('total'), data.get('wins'))
        selected, modes, metadata = selected_character_mode(profile,ident,name,stats,battle_type)
        data.update(metadata)
        data['selected_source'] = 'career' if battle_type=='all' or metadata['mode_source']=='account_single_family' else 'observed'
        return selected, [(name,selected)], modes, ident, name, data
    stats = account_counters(profile, battle_type)
    heroes, observed = [], False
    for ident, data in entries.items():
        archive = counters(data.get('total'), data.get('wins'))
        selected, _, metadata = selected_character_mode(profile,ident,data['name'],archive,battle_type)
        observed = observed or (battle_type!='all' and metadata['mode_source']=='observed')
        heroes.append((data['name'], selected))
    heroes.sort(key=lambda row: (-(row[1]['count'] or 0), row[0]))
    modes = [('全部模式',account_counters(profile,'all')),
             ('PVE',account_counters(profile,'pve')), ('PVP',account_counters(profile,'pvp'))]
    if battle_type != 'all':
        modes = [(name,data) for name,data in modes if name == battle_type.upper()]
    return stats, heroes, modes, None, '', {'selected_source':'observed' if observed else 'career'}
