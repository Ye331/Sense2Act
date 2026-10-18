"""文件输入适配；不写文件，不提交任务。"""
import hashlib
import json
from pathlib import Path

import pandas as pd

from signals.tender.normalization import normalize_text, parse_amount
from signals.tender.schemas import Announcement


FILE_ALIASES = {
    'id': ['id', 'project_id'],
    'title': ['title', '项目名称', '项目标题', '标题'],
    'buyer': ['buyer', '招标单位', '采购单位', '采购人'],
    'publish_date': ['publish_date', '信息发布时间', '发布时间', '发布日期'],
    'amount': ['amount', 'amount_yuan', '招标金额（元）', '金额', '招标金额', '预算金额', '预算金额（万元）'],
    'source_url': ['source_url', '官网查看地址', '详情链接', '来源链接', '链接'],
    'project_number': ['project_number', '项目编号'],
    'announcement_type': ['announcement_type', '招标阶段', '公告类型'],
    'content': ['content', '公告正文', '正文'],
    'project_summary': ['project_summary', '项目摘要'],
    'category': ['category', '项目领域'],
    'content_source_type': ['content_source_type', '正文来源类型'],
    'content_source_note': ['content_source_note', '正文来源说明'],
    'data_origin': ['data_origin'],
}


def _records_from_frame(frame: pd.DataFrame, source: Path, sheet: str = '') -> list[Announcement]:
    columns = {key: next((name for name in names if name in frame.columns), None)
               for key, names in FILE_ALIASES.items()}
    missing = [key for key in ['title', 'buyer', 'publish_date'] if columns[key] is None]
    if missing:
        raise ValueError(f'{source.name} 缺少列：{missing}')
    records = []
    for position, (_, row) in enumerate(frame.iterrows(), start=2):
        if row.isna().all() or all(not normalize_text(value) for value in row):
            continue
        values = {key: row[name] if name else None for key, name in columns.items()}
        # 现有 Excel 导出在内容区重复了表头；仅跳过明确的重复表头。
        if (normalize_text(values['title']) == columns['title'] and
                normalize_text(values['publish_date']) == columns['publish_date']):
            continue
        try:
            stamp = pd.Timestamp(values['publish_date'])
            if pd.isna(stamp):
                raise ValueError('发布日期缺失')
            values['publish_date'] = stamp.date().isoformat()
            values['amount'] = parse_amount(values['amount'],
                                            '万元' if '万元' in (columns['amount'] or '') else '元')
            if pd.isna(values['amount']):
                values['amount'] = None
            for key in values.keys() - {'amount', 'publish_date'}:
                values[key] = normalize_text(values[key])
            values['data_origin'] = values['data_origin'] or 'real'
            values['id'] = values['id'] or stable_announcement_id(values)
            values.update(source_file=source.name, source_sheet=sheet, source_row=position)
            records.append(Announcement.model_validate(values))
        except (ValueError, TypeError) as exc:
            raise ValueError(f'{source.name} 工作表 {sheet or "默认"} 第 {position} 行无效：{exc}') from exc
    return records


def stable_announcement_id(row: dict) -> str:
    source = row.get('source_url') or '|'.join(str(row.get(key, '')) for key in
        ['project_number', 'buyer', 'title', 'publish_date', 'announcement_type'])
    return 'R-' + hashlib.sha1(source.encode('utf-8')).hexdigest()[:16]


def load_announcements(path: str | Path) -> list[Announcement]:
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix == '.json':
        data = json.loads(path.read_text(encoding='utf-8-sig'))
        if not isinstance(data, list):
            raise ValueError('公告 JSON 文件必须为标准公告数组')
        if not data:
            return []
        return _records_from_frame(pd.DataFrame(data), path)
    if suffix == '.csv':
        return _records_from_frame(pd.read_csv(path, dtype=object, encoding='utf-8-sig'), path)
    if suffix in {'.xls', '.xlsx'}:
        sheets = pd.read_excel(path, sheet_name=None, dtype=object)
        return [row for sheet, frame in sheets.items() for row in _records_from_frame(frame, path, sheet)]
    raise ValueError('只支持 JSON、CSV、XLS 和 XLSX')
