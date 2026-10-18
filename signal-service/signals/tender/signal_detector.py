from __future__ import annotations

import math

import numpy as np
import pandas as pd

from .signal_explainer import build_explanation
from .project_identity import ProjectHistory


SIGNAL_COLUMNS = [
    'data_origin', 'real_history_count',
    'procurement_project_id', 'project_first_seen', 'first_announcement_id',
    'project_match_method', 'project_event', 'is_project_update', 'history_announcement_count',
    'project_id', 'title', 'buyer', 'publish_date', 'detection_date', 'amount_yuan', 'source_url',
    'project_summary', 'category', 'content_source_type', 'content_source_note',
    'source_file', 'source_sheet', 'source_row', 'history_cutoff', 'history_size',
    'relevance_score', 'relevance_threshold', 'relevance_pass', 'embedding_model', 'embedding_revision',
    'company_profile_used', 'amount_score', 'amount_ratio', 'historical_median_amount',
    'amount_history_count', 'amount_reliable', 'amount_status', 'novelty_score',
    'max_history_similarity', 'most_similar_history_project', 'most_similar_history_id',
    'most_similar_history_date', 'most_similar_history_url', 'novelty_history_count', 'novelty_reliable',
    'recent_count', 'historical_frequency', 'frequency_ratio', 'frequency_score',
    'frequency_history_windows_used', 'frequency_window', 'frequency_reliable',
    'amount_weight', 'novelty_weight', 'frequency_weight', 'priority_score', 'priority',
    'priority_confidence', 'min_history_count', 'high_priority_threshold', 'medium_priority_threshold',
    'evidence_status', 'priority_eligible', 'partial_score',
    'top_k', 'rank', 'is_signal', 'signal_reason',
]


def empty_signals() -> pd.DataFrame:
    return pd.DataFrame(columns=SIGNAL_COLUMNS)


def project_text(row: pd.Series) -> str:
    return str(row['title']).strip() + '\n' + str(row['project_summary']).strip()


class SignalDetector:
    def __init__(self, config: dict, weights: dict[str, float] | None = None, encoder=None):
        self.config = config
        self.settings = config['detector']
        self.weights = {key: float(value) for key, value in (weights or config['priority_weights']).items()}
        if set(self.weights) != {'amount', 'novelty', 'frequency'}:
            raise ValueError('Priority 只能包含 amount、novelty、frequency，相关性仅作 Gate')
        if any(not math.isfinite(value) or value < 0 for value in self.weights.values()) or not math.isclose(sum(self.weights.values()), 1.0):
            raise ValueError('Priority 权重必须非负且合计为 1')
        if not 0 <= float(self.settings['relevance_threshold']) <= 1:
            raise ValueError('relevance_threshold 必须在 0 至 1 之间')
        if int(self.settings['min_history_count']) < 1 or float(self.settings['amount_ratio_scale']) <= 0:
            raise ValueError('最少历史样本数及金额映射尺度必须大于 0')
        if int(config['display']['top_k']) < 1 or int(self.settings['frequency_window']) < 1 or int(self.settings['frequency_history_windows']) < 1:
            raise ValueError('Top-K、频率窗口及窗口数必须大于 0')
        if not 0 <= config['thresholds']['medium'] <= config['thresholds']['high'] <= 1:
            raise ValueError('优先级阈值必须满足 0 <= medium <= high <= 1')
        self.company_profile = config['business']['company_profile'].strip()
        if not self.company_profile:
            raise ValueError('company_profile 不能为空')
        if encoder is None:
            raise ValueError('检测器必须注入语义编码器')
        self.encoder = encoder

    def _amount(self, row: pd.Series, buyer_history: pd.DataFrame) -> dict:
        values = buyer_history['amount']
        values = values.loc[values.notna() & np.isfinite(values) & values.ge(0)]
        count = len(values)
        median = float(values.median()) if count else np.nan
        result = dict(historical_median_amount=median, amount_history_count=count,
                      amount_score=np.nan, amount_ratio=np.nan, amount_reliable=False)
        if count < int(self.settings['min_history_count']):
            result['amount_status'] = '同单位有效历史金额不足'
        elif not np.isfinite(row['amount']) or row['amount'] < 0:
            result['amount_status'] = '当前金额缺失或无效'
        elif median <= 0:
            result['amount_status'] = '历史金额中位数为零，无法计算倍数'
        else:
            ratio = float(row['amount']) / median
            result.update(amount_ratio=ratio, amount_score=1 - math.exp(-max(ratio - 1, 0) / float(self.settings['amount_ratio_scale'])),
                          amount_reliable=True, amount_status='可比较')
        return result

    def _novelty(self, row: pd.Series, buyer_history: pd.DataFrame) -> dict:
        references = buyer_history.loc[buyer_history['title'].str.strip().ne('') | buyer_history['project_summary'].str.strip().ne('')]
        count = len(references)
        result = dict(novelty_score=np.nan, max_history_similarity=np.nan, most_similar_history_project='',
                      most_similar_history_id='', most_similar_history_date=pd.NaT, most_similar_history_url='',
                      novelty_history_count=count, novelty_reliable=count >= int(self.settings['min_history_count']))
        if result['novelty_reliable']:
            query = self.encoder.encode([project_text(row)])[0]
            vectors = self.encoder.encode([project_text(item) for _, item in references.iterrows()])
            similarities = np.clip(vectors @ query, -1, 1)
            position = int(np.argmax(similarities))
            reference = references.iloc[position]
            similarity = float(similarities[position])
            result.update(novelty_score=float(np.clip(1 - similarity, 0, 1)), max_history_similarity=similarity,
                          most_similar_history_project=reference['title'], most_similar_history_id=reference['id'],
                          most_similar_history_date=reference['publish_date'], most_similar_history_url=reference.get('source_url', ''))
        return result

    def _frequency(self, row: pd.Series, buyer_history: pd.DataFrame, today: pd.DataFrame, day: pd.Timestamp) -> dict:
        window = int(self.settings['frequency_window'])
        recent_start = day - pd.Timedelta(days=window - 1)
        recent = int(buyer_history['publish_date'].ge(recent_start).sum())
        recent += int(today['buyer'].eq(row['buyer']).sum()) if row['buyer'] else 0
        observation_start = pd.Timestamp(self.config['date_range']['raw_start'])
        counts = []
        for offset in range(int(self.settings['frequency_history_windows'])):
            end = recent_start - pd.Timedelta(days=offset * window + 1)
            start = end - pd.Timedelta(days=window - 1)
            # 未覆盖的历史窗口不能视为零采购，只使用完整观察窗口。
            if start >= observation_start:
                counts.append(int(buyer_history['publish_date'].between(start, end).sum()))
        baseline = float(np.mean(counts)) if counts and row['buyer'] else np.nan
        reliable = bool(row['buyer']) and len(buyer_history) >= int(self.settings['min_history_count']) and np.isfinite(baseline) and baseline > 0
        ratio = recent / baseline if reliable else np.nan
        score = max(ratio - 1, 0) / (max(ratio - 1, 0) + 1) if reliable else np.nan
        return dict(recent_count=recent, historical_frequency=baseline, frequency_ratio=ratio, frequency_score=score,
                    frequency_history_windows_used=len(counts), frequency_window=window, frequency_reliable=bool(reliable))

    def detect_day(self, history: pd.DataFrame, today: pd.DataFrame, day: pd.Timestamp,
                   *, project_state: ProjectHistory | None = None) -> pd.DataFrame:
        """检测当天真实公告；只读取历史快照，不提前提交当日项目。"""
        if history['publish_date'].isna().any() or today['publish_date'].isna().any():
            raise ValueError('History / Current 不能包含缺失日期')
        if history['publish_date'].ge(day).any():
            raise ValueError('发现未来数据泄漏：History 必须严格早于检测日')
        if not today['publish_date'].eq(day).all():
            raise ValueError('Current 只能包含检测当天的数据')
        if today.empty:
            return empty_signals()
        history_announcements = len(history)
        # 使用已有历史及更新别名匹配当天公告，试算不会改变正式历史。
        state = project_state if project_state is not None else ProjectHistory(history)
        if state.announcement_count != len(history):
            raise ValueError('项目归并状态与 History 不一致')
        _, identities = state.fork().append(today)
        history = state.snapshot()
        identities = identities.set_index('id')
        new_today = today.loc[today['id'].map(identities['is_project_update']).eq(False)]
        texts = [project_text(row) for _, row in today.iterrows()]
        profile_vector = self.encoder.encode([self.company_profile])[0]
        relevance = np.clip(self.encoder.encode(texts) @ profile_vector, 0, 1)
        records = []
        threshold = float(self.settings['relevance_threshold'])
        for position, (_, row) in enumerate(today.iterrows()):
            record = {key: row.get(key, '') for key in ['title', 'buyer', 'publish_date', 'source_url', 'project_summary',
                       'category', 'content_source_type', 'content_source_note', 'source_file', 'source_sheet', 'source_row']}
            record.update(project_id=row['id'], amount_yuan=row['amount'], detection_date=day,
                          history_cutoff=day - pd.Timedelta(days=1), history_size=len(history),
                          relevance_score=float(relevance[position]), relevance_threshold=threshold,
                          relevance_pass=bool(relevance[position] >= threshold),
                          embedding_model=self.config['embedding']['model_name'], embedding_revision=self.config['embedding']['revision'],
                          company_profile_used=self.company_profile, min_history_count=int(self.settings['min_history_count']),
                          high_priority_threshold=float(self.config['thresholds']['high']),
                          medium_priority_threshold=float(self.config['thresholds']['medium']),
                          top_k=int(self.config['display']['top_k']), priority_score=np.nan, rank=np.nan,
                          is_signal=False, priority='未通过业务筛选', priority_confidence='未分析',
                          evidence_status='not_applicable', priority_eligible=False, partial_score=np.nan)
            record.update({f'{key}_weight': value for key, value in self.weights.items()})
            record.update(identities.loc[row['id']].to_dict())
            record['history_announcement_count'] = history_announcements
            record.update(data_origin='real', real_history_count=len(history))
            if record['is_project_update']:
                record.update(evidence_status='project_update', priority='项目更新', priority_confidence='不重复评分')
            elif record['relevance_pass']:
                # 同单位历史包含所有业务方向，不只保留通过业务筛选的项目。
                buyer_history = history.loc[history['buyer'].eq(row['buyer'])] if row['buyer'] else history.iloc[:0]
                record.update(self._amount(row, buyer_history))
                record.update(self._novelty(row, buyer_history))
                record.update(self._frequency(row, buyer_history, new_today, day))
                available_score = sum(self.weights[key] * record[f'{key}_score']
                                               for key in self.weights if record[f'{key}_reliable'] and self.weights[key] > 0)
                reliable_count = sum(bool(record[f'{key}_reliable']) for key in self.weights if self.weights[key] > 0)
                active_count = sum(value > 0 for value in self.weights.values())
                if reliable_count == active_count:
                    record.update(evidence_status='sufficient', priority_eligible=True,
                                  priority_confidence='充分', priority_score=available_score)
                    record['priority'] = ('高优先级' if available_score >= record['high_priority_threshold'] else
                                          '中优先级' if available_score >= record['medium_priority_threshold'] else '普通候选')
                elif reliable_count:
                    record.update(evidence_status='partial', priority_confidence='低置信度',
                                  partial_score=available_score, priority='部分历史，低置信度')
                else:
                    record.update(evidence_status='insufficient', priority_confidence='历史不足',
                                  priority='新客户/历史不足，待调查')
                    # 保留独立指标的真实可靠性，但不为完整排名补造依据。
            records.append(record)
        result = pd.DataFrame(records).reindex(columns=SIGNAL_COLUMNS)
        candidates = result.loc[result['priority_eligible']].sort_values(['priority_score', 'project_id'], ascending=[False, True])
        result.loc[candidates.index, 'rank'] = np.arange(1, len(candidates) + 1)
        result.loc[candidates.head(int(self.config['display']['top_k'])).index, 'is_signal'] = True
        result['signal_reason'] = result.apply(lambda row: '\n'.join(build_explanation(row.to_dict())), axis=1)
        return result.sort_values(['rank', 'project_id'], na_position='last').reset_index(drop=True)
