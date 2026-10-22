"""日期级机会筛选：当前北京时间决定时效，发布日期仅决定历史比较。"""
from copy import deepcopy
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from signals.tender.schemas import DetectionRequest
from signals.tender.loader import load_announcements


NOW = datetime(2026, 10, 22, 2, tzinfo=timezone.utc)


@pytest.mark.parametrize('deadline,status,eligible,reason', [
    ('2026-10-21', 'expired', False, '已截止'),
    ('2026-10-23', 'open', True, '日期层面未截止'),
    ('2026-10-22', 'needs_verification', True, '当日截止，具体时刻待核实'),
    (None, 'needs_verification', True, '截止日期未知，待核实'),
])
def test_deadline_uses_current_date_not_publication(service, request_data, deadline, status, eligible, reason):
    service.clock = lambda: NOW
    request_data['current'][0]['deadline'] = deadline
    result = service.detect(DetectionRequest.model_validate(request_data))
    row = result.records[0]
    assert row['evaluation_date'] == '2026-10-22'
    assert row['detection_date'] == '2026-10-04'
    assert row['history_cutoff'] == '2026-10-03'
    assert row['deadline'] == deadline
    assert row['deadline_status'] == status
    assert row['is_signal'] is eligible
    assert row['priority_eligible'] is eligible
    assert reason in row['signal_reason']
    assert row['priority_score'] is not None
    assert bool(result.signals) is eligible


def test_expired_high_score_does_not_take_topk_slot(service, request_data):
    service.clock = lambda: NOW
    service.config['display']['top_k'] = 1
    request_data['current'][0]['deadline'] = '2026-10-21'
    second = deepcopy(request_data['current'][0])
    second.update(id='C2', title='新影像平台', amount=200, deadline='2026-10-23',
                  source_url='https://example.com/C2')
    request_data['current'].append(second)
    result = service.detect(DetectionRequest.model_validate(request_data))
    rows = {r['project_id']: r for r in result.records}
    assert rows['C1']['priority_score'] > rows['C2']['priority_score']
    assert rows['C1']['rank'] is None
    assert rows['C2']['rank'] == 1
    assert [s.payload['project_id'] for s in result.signals] == ['C2']


def test_expired_history_still_supports_scoring(service, request_data):
    service.clock = lambda: NOW
    expected = service.detect(DetectionRequest.model_validate(request_data)).records[0]
    for row in request_data['history']:
        row['deadline'] = '2026-09-01'
    actual = service.detect(DetectionRequest.model_validate(request_data)).records[0]
    for key in ('amount_history_count', 'novelty_history_count', 'priority_score', 'frequency_score'):
        assert actual[key] == expected[key]


@pytest.mark.parametrize('deadline,expected', [('2026-10-21', False), ('2026-10-23', True)])
def test_new_buyer_cannot_bypass_deadline(service, request_data, deadline, expected):
    service.clock = lambda: NOW
    request_data['history'] = []
    request_data['current'][0]['deadline'] = deadline
    result = service.detect(DetectionRequest.model_validate(request_data))
    assert result.records[0]['priority_score'] == result.records[0]['relevance_score']
    assert bool(result.signals) is expected


@pytest.mark.parametrize('hour,minute,expected_date,status', [
    (15, 59, '2026-10-22', 'needs_verification'),
    (16, 0, '2026-10-23', 'expired'),
])
def test_beijing_midnight_boundary(service, request_data, hour, minute, expected_date, status):
    service.clock = lambda: datetime(2026, 10, 22, hour, minute, tzinfo=timezone.utc)
    request_data['current'][0]['deadline'] = '2026-10-22'
    row = service.detect(DetectionRequest.model_validate(request_data)).records[0]
    assert row['evaluation_date'] == expected_date
    assert row['deadline_status'] == status


def test_clock_read_once_per_detection(service, request_data):
    calls = []

    def clock():
        calls.append(1)
        return NOW

    service.clock = clock
    service.detect(DetectionRequest.model_validate(request_data))
    assert len(calls) == 1


@pytest.mark.parametrize('deadline', ['2026-02-30', '2026-10-22T12:00:00+08:00', '2026-1-2', '', 1792627200])
def test_deadline_requires_strict_calendar_date(request_data, deadline):
    request_data['current'][0]['deadline'] = deadline
    with pytest.raises(ValidationError):
        DetectionRequest.model_validate(request_data)


@pytest.mark.parametrize('extension', ['json', 'csv', 'xlsx'])
def test_file_input_preserves_optional_deadline(tmp_path, extension, service):
    import json
    import pandas as pd

    records = [dict(id='expired', title='医疗AI招标', buyer='医院甲', publish_date='2026-10-04',
                    deadline='2026-10-21', source_url='https://example.com/expired'),
               dict(id='unknown', title='影像系统招标', buyer='医院甲', publish_date='2026-10-04',
                    source_url='https://example.com/unknown')]
    path = tmp_path / f'announcements.{extension}'
    if extension == 'json':
        path.write_text(json.dumps(records), encoding='utf-8')
    elif extension == 'csv':
        pd.DataFrame(records).to_csv(path, index=False)
    else:
        pd.DataFrame(records).to_excel(path, index=False)
    rows = load_announcements(path)
    assert rows[0].deadline.isoformat() == '2026-10-21'
    assert rows[1].deadline is None
    service.clock = lambda: NOW
    request = DetectionRequest(company_id='42', company_profile='医疗信息系统', detection_date='2026-10-04',
                               history_start_date='2026-05-01', history=[], current=rows)
    result = service.detect(request)
    assert [s.payload['project_id'] for s in result.signals] == ['unknown']


def test_http_schema_exposes_optional_deadline_without_client_clock(service):
    from application import create_app

    schemas = create_app(service).openapi()['components']['schemas']
    announcement = schemas['Announcement']
    assert 'deadline' not in announcement['required']
    assert {'type': 'string', 'format': 'date'} in announcement['properties']['deadline']['anyOf']
    assert 'evaluation_date' not in schemas['DetectionRequest']['properties']
