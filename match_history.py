"""Persist observed own-account matches; this does not fetch unavailable server history."""
from collections import defaultdict
import json
import os
from pathlib import Path
import tempfile
import threading

FIELDS = ('time', 'hero_id', 'hero', 'mode_id', 'mode', 'rank', 'win', 'replay', 'damage', 'transfer')


def signature(row):
    timestamp = int(row.get('time') or 0)
    if timestamp <= 0:
        return None
    return (timestamp, row.get('hero_id') or row.get('hero'), row.get('mode_id') or row.get('mode'))


def merge_rows(existing, incoming):
    rows = [dict(r) for r in existing]
    by_replay = {str(r['replay']): r for r in rows if r.get('replay')}
    by_signature = defaultdict(dict)
    for row in rows:
        if signature(row):
            by_signature[signature(row)][id(row)] = row
    for item in incoming:
        item = {k: v for k, v in item.items() if k in FIELDS}
        replay = str(item.get('replay') or '')
        target = by_replay.get(replay) if replay else None
        if target is None and signature(item):
            candidates = [r for r in by_signature[signature(item)].values()
                          if not replay or not r.get('replay')]
            if len(candidates) == 1:
                target = candidates[0]
        if target is None:
            target = item
            rows.append(target)
        else:
            for key, value in item.items():
                if value is None or value == '':
                    continue
                if key in ('time', 'hero_id', 'mode_id', 'rank') and value == 0 and target.get(key):
                    continue
                target[key] = value
        if target.get('replay'):
            by_replay[str(target['replay'])] = target
        if signature(target):
            by_signature[signature(target)][id(target)] = target
    rows.sort(key=lambda row: -int(row.get('time') or 0))
    return rows


class MatchHistoryStore:
    def __init__(self, directory, limit=5000):
        if limit < 1:
            raise ValueError('history limit must be positive')
        self.directory, self.limit = Path(directory), limit
        self.lock = threading.RLock()

    def merge_profile(self, profile):
        if not profile.get('uid') or profile.get('source') == 'public':
            return profile
        uid = int(profile['uid'])
        if uid <= 0:
            raise ValueError('invalid player UID')
        path = self.directory / f'cn_{uid}.json'
        with self.lock:
            try:
                old = json.loads(path.read_text(encoding='utf-8'))
                if not isinstance(old, list) or not all(isinstance(r, dict) for r in old):
                    raise ValueError('invalid match history')
            except FileNotFoundError:
                old = []
            rows = merge_rows(old, profile.get('recent') or [])[:self.limit]
            # Undated rows without replay ID have no reliable identity; do not accumulate duplicates.
            saved = [r for r in rows if r.get('replay') or signature(r)]
            self.directory.mkdir(parents=True, exist_ok=True)
            fd, temporary = tempfile.mkstemp(prefix='.matches-', suffix='.tmp', dir=self.directory)
            try:
                with os.fdopen(fd, 'w', encoding='utf-8') as file:
                    json.dump(saved, file, ensure_ascii=False)
                os.replace(temporary, path)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
        result = dict(profile, recent=rows, history_source='local', history_limit=self.limit)
        return result
