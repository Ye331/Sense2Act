"""公告标准化；文件中的别名和单位只在这里转换。"""
from __future__ import annotations

import html
import re

import numpy as np
import pandas as pd

from datetime import date

from signals.tender.schemas import Announcement

def normalize_text(value: object) -> str:
    if value is None or pd.isna(value):
        return ""
    text = str(value).replace("\u3000", " ").strip()
    text = text.replace("（", "(").replace("）", ")")
    text = re.sub(r"^[·•﹒\-—_\s]+", "", text)
    return re.sub(r"\s+", " ", text).strip()


def clean_body(value: object) -> str:
    text = normalize_text(value)
    text = re.sub(r"<(script|style)\b[^>]*>.*?</\1>", " ", text, flags=re.I | re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    return normalize_text(html.unescape(text).replace("\u200b", "").replace("\ufeff", ""))


def normalize_buyer(value: object, aliases: dict[str, str]) -> str:
    buyer = normalize_text(value)
    buyer = re.sub(r"^(采购人|招标人|招标单位)[：:]", "", buyer).strip()
    buyer = re.sub(r"\s*[（(]本级[）)]$", "", buyer).strip()
    normalized_aliases = {normalize_text(key): normalize_text(val) for key, val in aliases.items()}
    return normalized_aliases.get(buyer, buyer)


def parse_amount(value: object, default_unit: str = "元") -> float:
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return np.nan
    if isinstance(value, (int, float, np.number)):
        number = float(value)
        multiplier = 100_000_000 if "亿" in default_unit else (10_000 if "万" in default_unit else 1)
        return number * multiplier if np.isfinite(number) and number >= 0 else np.nan
    if re.match(r"\s*[-−]", str(value)):
        return np.nan
    text = normalize_text(value).replace(",", "").replace("，", "").replace("￥", "").replace("¥", "")
    # 金额区间或多个数字无法确定唯一金额，保留为缺失值。
    if re.search(r"\d\s*(?:-|~|～|至)\s*\d", text) or text.startswith("-") or "%" in text:
        return np.nan
    matches = re.findall(r"\d+(?:\.\d+)?", text)
    if len(matches) != 1:
        return np.nan
    number = float(matches[0])
    unit = "亿" if "亿" in text else ("万" if "万" in text else ("元" if "元" in text else default_unit))
    if "亿" in unit:
        number *= 100_000_000
    elif "万" in unit:
        number *= 10_000
    return number if np.isfinite(number) and number >= 0 else np.nan


def normalize_batch(history: list[Announcement], current: list[Announcement], detection_date: date, history_start_date: date, aliases: dict) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    rows = []
    seen_ids = {}
    duplicates = 0
    for row in history + current:
        values = row.model_dump(mode='python')
        values['title'] = normalize_text(values['title'])
        values['buyer'] = normalize_buyer(values['buyer'], aliases)
        if not values['title']:
            raise ValueError('清洗后的项目标题不能为空')
        body = clean_body(values.pop('content'))
        summary = clean_body(values['project_summary'])
        note = normalize_text(values['content_source_note'])
        if not values['buyer']:
            note = (note + '；' if note else '') + '采购单位缺失，无法建立同单位历史比较'
        if not summary:
            summary = body or f"{values['title']}；采购单位：{values['buyer']}"
        if not body and not values['project_summary'].strip():
            note = (note + '；' if note else '') + '正文缺失；摘要仅依据标题与采购单位整理'
        values['project_summary'] = summary
        values['content_source_note'] = note
        values['content_source_type'] = values['content_source_type'] or ('原始正文' if body else '提供的摘要' if row.project_summary else '字段摘要')
        values['publish_date'] = pd.Timestamp(row.publish_date)
        values['amount'] = float(row.amount) if row.amount is not None else np.nan
        # 文件来源/行号不影响相同公告判定。
        comparable = row.model_dump(mode='json', exclude={'source_file', 'source_sheet', 'source_row'})
        if row.id in seen_ids:
            if comparable != seen_ids[row.id]:
                raise ValueError(f'公告 ID 冲突：{row.id}，同一 ID 对应不同内容')
            duplicates += 1
            continue
        seen_ids[row.id] = comparable
        rows.append(values)
    columns = list(Announcement.model_fields.keys() - {'content'})
    frame = pd.DataFrame(rows, columns=columns)
    frame['publish_date'] = pd.to_datetime(frame['publish_date'])
    frame['amount'] = pd.to_numeric(frame['amount'])
    frame = frame.sort_values(['publish_date', 'id'], kind='stable')
    url_duplicate = frame['source_url'].ne('') & frame.duplicated('source_url')
    compound_duplicate = frame.duplicated(['title', 'buyer', 'publish_date', 'announcement_type', 'amount'])
    duplicate_mask = url_duplicate | compound_duplicate
    duplicates += int(duplicate_mask.sum())
    frame = frame.loc[~duplicate_mask].reset_index(drop=True)
    day = pd.Timestamp(detection_date)
    report = {'input_rows': len(history) + len(current), 'duplicate_rows_removed': duplicates,
              'history_rows': int(frame['publish_date'].lt(day).sum()),
              'current_rows': int(frame['publish_date'].eq(day).sum()),
              'history_start_date': history_start_date.isoformat()}
    return frame.loc[frame['publish_date'].lt(day)].copy(), frame.loc[frame['publish_date'].eq(day)].copy(), report
