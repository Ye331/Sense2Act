from copy import deepcopy
import math

import numpy as np
import pytest
from pydantic import ValidationError

from signals.tender.schemas import DetectionRequest
from signals.tender.service import PAYLOAD_FIELDS, TenderService
from signals.common.task_client import TaskClient


def test_real_history_scores_and_minimal_payload(service, batch):
    result = service.detect(batch)
    record = result.records[0]
    assert record['amount_history_count'] == 3
    assert record['historical_median_amount'] == 200
    assert record['amount_score'] == pytest.approx(1 - math.exp(-1))
    assert record['novelty_score'] == pytest.approx(0.4)
    assert record['priority_score'] == pytest.approx(.5 + .3 * (1 - math.exp(-1)) + .2 * .4)
    assert record['scoring_weight_sum'] == pytest.approx(1)
    assert record['frequency_history_windows_used'] == 4
    assert record['frequency_ratio'] == pytest.approx(4 / 3)
    assert record['frequency_score'] == pytest.approx(0.25)
    signal = result.signals[0]
    assert set(signal.payload) == set(PAYLOAD_FIELDS)
    assert signal.payload['procurement_project_id'] == 'C1'
    assert signal.payload['frequency_reliable'] is True
    assert '2026-10-03' in signal.payload['signal_reason']
    assert 'https://example.com/H1' in signal.payload['signal_reason']
    assert result.submissions[0].status == 'not_submitted'


def test_new_buyer_ranks_by_relevance_without_fabricating_history(service, request_data):
    request_data['history'] = []
    result = service.detect(DetectionRequest.model_validate(request_data))
    assert len(result.signals) == 1
    record = result.records[0]
    for key in ['amount', 'novelty', 'frequency']:
        assert record[f'{key}_score'] is None
        assert record[f'{key}_reliable'] is False
    assert record['priority_score'] == pytest.approx(record['relevance_score'])
    assert record['scoring_weight_sum'] == pytest.approx(.5)
    assert record['priority_eligible'] is True
    assert record['evidence_status'] == 'partial'
    assert record['priority_confidence'] == '低置信度'
    assert '历史指标依据不完整' in record['signal_reason']
    assert 'NaN' not in result.model_dump_json()


def test_missing_amount_ranks_with_relevance_and_novelty(service, request_data):
    request_data['current'][0]['amount'] = None
    result = service.detect(DetectionRequest.model_validate(request_data))
    assert len(result.signals) == 1
    record = result.records[0]
    assert record['amount_score'] is None
    assert record['novelty_score'] == pytest.approx(.4)
    assert record['evidence_status'] == 'partial'
    assert record['priority_score'] == pytest.approx((.5 + .2 * .4) / .7)
    assert record['scoring_weight_sum'] == pytest.approx(.7)


def test_uncovered_frequency_windows_are_not_zero(service, request_data):
    request_data['history_start_date'] = '2026-09-10'
    for index, row in enumerate(request_data['history']):
        row['publish_date'] = f'2026-09-{11 + index}'
    result = service.detect(DetectionRequest.model_validate(request_data))
    assert result.records[0]['frequency_history_windows_used'] == 0
    assert result.signals[0].payload['frequency_score'] is None
    assert result.signals[0].payload['frequency_reliable'] is False


def test_duplicate_announcements_do_not_raise_frequency(service, request_data):
    expected = service.detect(DetectionRequest.model_validate(request_data))
    request_data['history'].append(deepcopy(request_data['history'][0]))
    request_data['current'].append(deepcopy(request_data['current'][0]))
    actual = service.detect(DetectionRequest.model_validate(request_data))
    assert actual.normalization['duplicate_rows_removed'] == 2
    assert actual.records[0]['recent_count'] == expected.records[0]['recent_count']
    assert actual.signals == expected.signals


def test_project_update_uses_identity_not_new_purchase(service, request_data):
    request_data['history'][0]['project_number'] = 'P-001'
    request_data['current'][0]['project_number'] = 'P-001'
    result = service.detect(DetectionRequest.model_validate(request_data))
    assert not result.signals
    assert result.records[0]['procurement_project_id'] == 'H1'
    assert result.records[0]['is_project_update'] is True


def test_different_number_and_year_are_separate_projects(service, request_data):
    request_data['history'][0].update(title='2025年医疗AI采购公告', project_number='2025-001')
    request_data['current'][0].update(title='2026年医疗AI采购公告', project_number='2026-001')
    result = service.detect(DetectionRequest.model_validate(request_data))
    assert result.records[0]['procurement_project_id'] == 'C1'


def test_topk_without_minimum_threshold(config, service, request_data):
    config['display']['top_k'] = 1
    config['thresholds']['medium'] = .65
    request_data['current'][0]['amount'] = 200
    second = deepcopy(request_data['current'][0])
    second.update(id='C2', title='新影像系统采购', source_url='https://example.com/C2')
    request_data['current'].append(second)
    service.config = config
    result = service.detect(DetectionRequest.model_validate(request_data))
    assert len(result.signals) == 1
    assert result.signals[0].payload['project_id'] == 'C1'
    assert result.records[0]['priority_score'] < .65


def test_new_buyer_still_requires_relevance_gate(config, request_data):
    class LowRelevance:
        def encode(self, texts):
            return np.array([[1., 0.] if text == '医疗信息系统' else [0., 1.] for text in texts])

    request_data['history'] = []
    service = TenderService(config, encoder=LowRelevance(), task_client=TaskClient(url=''))
    result = service.detect(DetectionRequest.model_validate(request_data))
    assert result.signals == []
    assert result.records[0]['relevance_pass'] is False
    assert result.records[0]['priority_score'] is None


def test_relevance_orders_new_buyers_in_existing_topk(config, request_data):
    class RankedRelevance:
        def encode(self, texts):
            return np.array([[.7, math.sqrt(.51)] if text.startswith('较低') else [.9, math.sqrt(.19)]
                             if text.startswith('新医疗') else [1., 0.] for text in texts])

    request_data['history'] = []
    second = deepcopy(request_data['current'][0])
    second.update(id='C2', title='较低相关采购', source_url='https://example.com/C2')
    request_data['current'].append(second)
    config['display']['top_k'] = 1
    service = TenderService(config, encoder=RankedRelevance(), task_client=TaskClient(url=''))
    result = service.detect(DetectionRequest.model_validate(request_data))
    assert [r['priority_score'] for r in result.records] == pytest.approx([.9, .7])
    assert [s.payload['project_id'] for s in result.signals] == ['C1']


def test_signal_id_stable_and_changes_with_result(service, batch, request_data):
    first = service.detect(batch).signals[0].signal_id
    assert service.detect(batch).signals[0].signal_id == first
    request_data['current'][0]['amount'] = 1000
    assert service.detect(DetectionRequest.model_validate(request_data)).signals[0].signal_id != first


@pytest.mark.parametrize('change', ['future', 'old_current', 'simulated', 'invalid_date', 'negative_amount'])
def test_bad_inputs_rejected_before_detection(request_data, change):
    if change == 'future':
        request_data['history'][0]['publish_date'] = '2026-10-04'
    elif change == 'old_current':
        request_data['current'][0]['publish_date'] = '2026-10-03'
    elif change == 'simulated':
        request_data['history'][0]['data_origin'] = 'simulated'
    elif change == 'invalid_date':
        request_data['current'][0]['publish_date'] = '2026-02-30'
    else:
        request_data['current'][0]['amount'] = -1
    with pytest.raises(ValidationError):
        DetectionRequest.model_validate(request_data)


def test_conflicting_id_rejected(service, request_data):
    duplicate = deepcopy(request_data['current'][0])
    duplicate['amount'] = 500
    request_data['current'].append(duplicate)
    with pytest.raises(ValueError, match='ID 冲突'):
        service.detect(DetectionRequest.model_validate(request_data))


def test_missing_source_blocks_submission(service, request_data):
    request_data['current'][0]['source_url'] = ''
    result = service.detect(DetectionRequest.model_validate(request_data))
    assert result.records[0]['is_signal'] is True
    assert result.records[0]['submission_eligible'] is False
    assert result.signals == []


def test_negative_similarity_novelty_is_bounded(config, batch):
    class OppositeHistory:
        def encode(self, texts):
            return np.array([[-1., 0.] if text.startswith('历史') else [1., 0.] for text in texts])
    result = TenderService(config, encoder=OppositeHistory(), task_client=TaskClient(url='')).detect(batch)
    assert result.signals[0].payload['novelty_score'] == 1


def test_empty_batch_does_not_load_model(config, request_data):
    request_data['current'] = []
    config['embedding']['local_path'] = 'missing-model'
    result = TenderService(config).detect(DetectionRequest.model_validate(request_data))
    assert result.records == []


def test_unknown_buyer_preserved_but_not_investigated(service, request_data):
    request_data['current'][0]['buyer'] = ''
    result = service.detect(DetectionRequest.model_validate(request_data))
    assert result.records[0]['amount_score'] is None
    assert result.signals == []
    assert '采购单位缺失' in result.records[0]['signal_reason']


def test_frequency_weight_requires_reliable_frequency(config, service, request_data):
    config['priority_weights'] = {'amount': 0., 'novelty': 0., 'frequency': 1.}
    request_data['history_start_date'] = '2026-09-10'
    for index, row in enumerate(request_data['history']):
        row['publish_date'] = f'2026-09-{11 + index}'
    service.config = config
    result = service.detect(DetectionRequest.model_validate(request_data))
    assert result.signals == []
    assert result.records[0]['amount_reliable'] is True
    assert result.records[0]['frequency_reliable'] is False
