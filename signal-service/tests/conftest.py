from copy import deepcopy

import numpy as np
import pytest

from signals.common.task_client import TaskClient
from signals.tender.schemas import DetectionRequest
from core.config import load_config
from signals.tender.service import TenderService


class KnownVectors:
    """可控语义向量验证算法；不替代正式模型。"""
    def encode(self, texts):
        return np.array([[0.6, 0.8] if text.startswith('历史') else [1.0, 0.0] for text in texts])


@pytest.fixture
def config():
    return deepcopy(load_config())


@pytest.fixture
def request_data():
    def row(id, title, day, amount):
        return dict(id=id, title=title, buyer='医院甲', publish_date=day, amount=amount,
                    source_url=f'https://example.com/{id}', project_summary=title)
    return dict(company_id='42', company_profile='医疗信息系统', detection_date='2026-10-04',
                history_start_date='2026-05-01', history=[
                    row('H1', '历史影像平台', '2026-07-05', 100),
                    row('H2', '历史临床系统', '2026-07-06', 200),
                    row('H3', '历史数据中心', '2026-08-05', 300),
                ], current=[row('C1', '新医疗AI采购', '2026-10-04', 800)])


@pytest.fixture
def service(config):
    return TenderService(config, encoder=KnownVectors(), task_client=TaskClient(url=''))


@pytest.fixture
def batch(request_data):
    return DetectionRequest.model_validate(request_data)
