"""Resolve characters from the project's data tables, never guessed aliases."""
import json
from pathlib import Path
import unicodedata


def normalized(value):
    return ''.join(unicodedata.normalize('NFKC', str(value)).split()).casefold()


class CharacterLookup:
    def __init__(self, directory=None):
        directory = directory or Path(__file__).resolve().parent / 'assets' / 'data'
        self.names, self.aliases = {}, {}
        for filename in ('heroes.json', 'character_ids.json'):
            for entry in json.loads((directory / filename).read_text(encoding='utf-8')):
                ident = int(entry['id'])
                self.names[ident] = entry['name']
                for alias in (str(ident), entry['name'], entry.get('en'), entry.get('title')):
                    if alias:
                        self.aliases.setdefault(normalized(alias), set()).add(ident)

    def resolve(self, value, rows=()):
        query = normalized(value)
        matches = set(self.aliases.get(query, ()))
        for row in rows:
            if row.get('hero_id') and (normalized(row.get('hero', '')) == query or str(row['hero_id']) == query):
                matches.add(int(row['hero_id']))
        if len(matches) > 1:
            raise ValueError('角色名称不唯一，请使用角色 ID。')
        if matches:
            ident = matches.pop()
            runtime_name = next((r['hero'] for r in rows if r.get('hero_id') and int(r['hero_id']) == ident), str(value).strip())
            return ident, self.names.get(ident, runtime_name)
        # Runtime tables can introduce names before the bundled character list updates.
        for row in rows:
            if normalized(row.get('hero', '')) == query and query:
                return None, row['hero']
        raise ValueError('未识别角色，请使用完整中文名、角色 ID 或英文名，例如 芬妮 / 102 / Fanny。')

    def matches(self, row, ident, name):
        if ident is not None and row.get('hero_id'):
            return int(row['hero_id']) == ident
        return normalized(row.get('hero', '')) == normalized(name)


CHARACTERS = CharacterLookup()
