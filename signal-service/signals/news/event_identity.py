"""复用监测评分；按主体、规则、动作与标题归组。"""
from dataclasses import replace
from hashlib import sha256
import json
from urllib.parse import urlsplit

from .core.engine import MonitorEngine
from .core.matching import contains_any, match_subjects
from .core.normalize import normalize_text


class NewsMonitorEngine(MonitorEngine):
    def _route_literal(self, entry, now):
        original = self._originals.get(entry.item.content_id, {})
        ext = original.get('ext') or {}
        anchors = ext.get('newsRelationAnchors')
        subjects = ext.get('newsRelatedSubjects')
        if anchors and subjects:
            entry.clauses = anchors
            hits = []
            for entity in [e for e in self.config.entities if e.entity_id in subjects]:
                occurrences = [replace(hit, clause_index=index) for index, context in enumerate(anchors)
                               for hit in match_subjects([context], [entity])]
                if occurrences:
                    def rank(hit):
                        actions = sum(bool(contains_any(hit.clause_text, family.actions))
                                      for family in self.config.families if entity.type in family.applies_to)
                        return (actions, hit.strength, -hit.clause_index)
                    hits.append(max(occurrences, key=rank))
            entry.subject_hits = hits
        super()._route_literal(entry, now)
        for index, hit in enumerate(entry.fast_hits):
            if not hit.object_unverified:
                continue
            clause = entry.clauses[hit.clause_index]
            position = clause.find(hit.action)
            before = clause[max(0, position - self.config.settings.object_window_chars):position]
            word = contains_any(before, hit.family.object_words)
            # 长标题中宾语在动作前后都可出现，不受英文字长的 32 字窗口误伤。
            title = normalize_text(original.get('title') or '')
            if word is None and contains_any(title, hit.family.actions):
                word = contains_any(title, hit.family.object_words)
            if word and not contains_any(before, hit.family.notice_words):
                entry.fast_hits[index] = replace(hit, object_word=word, object_unverified=False)

    def _fast_candidate(self, entry, action_hit, subject):
        subject = next((hit for hit in entry.subject_hits if hit.clause_index == action_hit.clause_index
                        and hit.entity.type in action_hit.family.applies_to), subject)
        return super()._fast_candidate(entry, action_hit, subject)

    def run_tick(self, raw_items, now=None):
        self._news_samples = {}
        self._originals = {item['contentId']: item for item in raw_items}
        summary = super().run_tick(raw_items, now=now)
        state = self.store.load_state()
        for key, sample in self._news_samples.items():
            state.setdefault('groups', {}).setdefault(key, {}).setdefault('sampleTitle', sample)
        self.store.save_state(state)
        return summary

    def _resolve_group_key(self, entry, group_samples):
        scope = [sorted(h.entity.entity_id for h in entry.subject_hits),
                 sorted((h.family.id, h.action) for h in entry.fast_hits)]
        prefix = 'newsgrp_' + sha256(json.dumps(scope).encode()).hexdigest()[:12] + '_'
        samples = {key: value for key, value in group_samples.items() if key.startswith(prefix)}
        original = entry.title_hash
        entry.title_hash = prefix + original
        try:
            key = super()._resolve_group_key(entry, samples)
        finally:
            entry.title_hash = original
        group_samples.update(samples)
        self._news_samples[key] = samples[key]
        return key

    def _independent_sources(self, members):
        sources, texts = set(), set()
        for member in members:
            item = self._originals[member.entry.item.content_id]
            text = normalize_text(item.get('summary') or item.get('title') or '')
            if text in texts:
                continue  # 同一通稿的转载不增加共振分。
            texts.add(text)
            ext = item.get('ext') or {}
            source = ext.get('sourceName') or urlsplit(item.get('url') or '').hostname
            if source:
                sources.add(normalize_text(source))
        return sorted(sources)

    def _score(self, anchor, members, state, platforms, now):
        return super()._score(anchor, members, state, self._independent_sources(members), now)

    def _build_alert(self, **options):
        alert = super()._build_alert(**options)
        sources = self._independent_sources(options['members'])
        alert.resonance['sourceCount'] = len(sources)
        alert.resonance['independentSources'] = sources
        return alert
