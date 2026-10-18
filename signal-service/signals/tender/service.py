"""检测、精简输出和提交的业务入口。"""
from copy import deepcopy
from datetime import date, datetime
from hashlib import sha256
import json

import numpy as np
import pandas as pd

from core.config import load_config
from core.semantic_encoder import get_shared_encoder
from signals.common.schemas import SignalSubmission
from signals.common.task_client import TaskClient
from signals.tender.normalization import normalize_batch
from signals.tender.schemas import DetectionRequest, DetectionResponse
from signals.tender.signal_detector import SignalDetector

PAYLOAD_FIELDS = [
    'project_id', 'procurement_project_id', 'title', 'buyer', 'publish_date', 'amount_yuan',
    'project_summary', 'source_url', 'signal_reason', 'relevance_score', 'amount_score',
    'novelty_score', 'frequency_score', 'amount_reliable', 'novelty_reliable', 'frequency_reliable',
]



def json_value(value):
    if isinstance(value, dict):
        return {key: json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_value(item) for item in value]
    if value is None or value is pd.NA or value is pd.NaT:
        return None
    if isinstance(value, (pd.Timestamp, datetime, date)):
        return value.strftime('%Y-%m-%d')
    if isinstance(value, np.generic):
        return json_value(value.item())
    if isinstance(value, float) and not np.isfinite(value):
        return None
    return value


def make_signal(company_id: str, record: dict) -> SignalSubmission:
    payload = {key: record[key] for key in PAYLOAD_FIELDS}
    for key in ['amount_reliable', 'novelty_reliable', 'frequency_reliable']:
        payload[key] = bool(payload[key]) if payload[key] is not None else False
    if record.get('category'):
        payload['category'] = record['category']
    if record.get('content_source_note') and record['content_source_note'] not in payload['project_summary']:
        payload['project_summary'] += '；' + record['content_source_note']
    canonical = json.dumps({'company_id': company_id, 'kind': 'tender', 'payload': payload},
                           sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False)
    return SignalSubmission(signal_id='SIG_TENDER_' + sha256(canonical.encode('utf-8')).hexdigest(),
                            company_id=company_id, kind='tender', payload=payload)


class TenderService:
    def __init__(self, config: dict | None = None, encoder=None, task_client: TaskClient | None = None):
        self.config = config if config is not None else load_config()
        self.encoder = encoder
        self.task_client = task_client if task_client is not None else TaskClient()

    def detect(self, request: DetectionRequest) -> DetectionResponse:
        config = deepcopy(self.config)
        config['business'] = {'company_profile': request.company_profile}
        config['date_range'] = {'raw_start': request.history_start_date.isoformat()}
        history, current, report = normalize_batch(request.history, request.current, request.detection_date, request.history_start_date, config.get('buyer_aliases', {}))
        # 空批次无需加载模型；接口仍要求采集方确认 Current 是完整批次。
        if current.empty:
            records = []
        else:
            encoder = self.encoder if self.encoder is not None else get_shared_encoder(config['embedding'])
            detector = SignalDetector(config, encoder=encoder)
            frame = detector.detect_day(history, current, pd.Timestamp(request.detection_date))
            records = json_value(frame.to_dict(orient='records'))
        signals = []
        for record in records:
            record['submission_eligible'] = bool(record['is_signal'] and record['source_url'])
            if record['is_signal'] and not record['source_url']:
                record['signal_reason'] += '\n缺少原公告链接，保留入选结果，补齐来源后再提交。'
            if record['submission_eligible']:
                signals.append(make_signal(request.company_id, record))
        submissions = [self.task_client.submit(signal) for signal in signals]
        return DetectionResponse(company_id=request.company_id, detection_date=request.detection_date.isoformat(),
                                 records=records, signals=signals, submissions=submissions, normalization=report)
