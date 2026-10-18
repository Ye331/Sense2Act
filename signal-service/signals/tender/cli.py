"""命令行输入适配。只在明确指定 --output 时保存文件。"""
import argparse
from datetime import date
import json
from pathlib import Path
import sys

from core.config import load_config
from signals.common.schemas import SignalSubmission, SubmissionResult
from signals.common.task_client import TaskClient
from signals.tender.loader import load_announcements
from signals.tender.schemas import DetectionRequest, DetectionResponse
from signals.tender.service import TenderService


def retry_file(path: Path, client: TaskClient) -> list[SubmissionResult]:
    data = json.loads(path.read_text(encoding='utf-8-sig'))
    if isinstance(data, dict):
        response = DetectionResponse.model_validate(data)
        pending = {item.signal_id for item in response.submissions if item.status != 'submitted'}
        signals = [signal for signal in response.signals if signal.signal_id in pending]
    elif isinstance(data, list):
        signals = [SignalSubmission.model_validate(item) for item in data]
    else:
        raise ValueError('重试输入必须是检测结果对象或信号数组')
    return [client.submit(signal) for signal in signals]


def emit_result(data, output: str | None):
    text = json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False)
    if output is None:
        print(text)
    else:
        path = Path(output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8')


def main():
    parser = argparse.ArgumentParser(description='招投标信号检测')
    sub = parser.add_subparsers(dest='command', required=True)
    detect = sub.add_parser('detect')
    source = detect.add_mutually_exclusive_group(required=True)
    source.add_argument('--request', help='完整标准检测请求 JSON')
    source.add_argument('--announcements', help='真实公告文件，自动分为历史与当日')
    detect.add_argument('--company-id')
    detect.add_argument('--company-profile')
    detect.add_argument('--detection-date')
    detect.add_argument('--history-start-date')
    detect.add_argument('--config')
    detect.add_argument('--output', help='可选，仅指定时保存一份完整结果 JSON')
    retry = sub.add_parser('retry')
    retry.add_argument('--signals', required=True, help='检测结果 JSON 或原信号数组')
    retry.add_argument('--output', help='可选，仅指定时保存提交结果')
    args = parser.parse_args()
    if args.command == 'retry':
        results = retry_file(Path(args.signals), TaskClient())
        emit_result([r.model_dump(mode='json') for r in results], args.output)
        return
    if args.request:
        request = DetectionRequest.model_validate_json(Path(args.request).read_text(encoding='utf-8-sig'))
    else:
        required = ['company_id', 'company_profile', 'detection_date', 'history_start_date']
        if any(not getattr(args, key) for key in required):
            parser.error('--announcements 需要企业 ID、企业画像、检测日和历史覆盖起始日')
        rows = load_announcements(args.announcements)
        day, start = date.fromisoformat(args.detection_date), date.fromisoformat(args.history_start_date)
        history = [r for r in rows if start <= r.publish_date < day]
        current = [r for r in rows if r.publish_date == day]
        skipped = len(rows) - len(history) - len(current)
        if skipped:
            print(f'覆盖区间外公告 {skipped} 条未进入本次检测', file=sys.stderr)
        request = DetectionRequest(company_id=args.company_id, company_profile=args.company_profile,
                                   detection_date=day, history_start_date=start, history=history, current=current)
    response = TenderService(load_config(args.config)).detect(request)
    emit_result(response.model_dump(mode='json'), args.output)


if __name__ == '__main__':
    main()
