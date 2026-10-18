"""按时间归并采购项目，保留公告记录，避免重复统计。"""
from __future__ import annotations

import re
import unicodedata

import pandas as pd


IDENTITY_COLUMNS = [
    'id', 'procurement_project_id', 'project_first_seen', 'first_announcement_id',
    'project_match_method', 'project_event', 'is_project_update',
]


def text(value) -> str:
    return '' if pd.isna(value) else unicodedata.normalize('NFKC', str(value)).strip()


def project_number(row) -> str:
    value = text(row.get('project_number', ''))
    if value.lower() in {'', 'nan', 'none', '-', '/', '未披露', '无'}:
        match = re.search(r'项目编号\s*[:：]?\s*([^()（）]+)', text(row.get('title', '')))
        value = match.group(1) if match else ''
    return re.sub(r'\s+', '', value).upper()


def project_name(row) -> str:
    value = text(row.get('title', ''))
    value = re.sub(r'\(项目编号[^)]*\)', '', value)
    value = re.sub(r'\((?:第?[一二三四五六七八九十\d]+次(?:招标|采购)?|重新招标|重招)\)', '', value)
    # 只去除公告阶段后缀；年份、批次、标包及采购范围仍用于区分项目。
    suffix = r'(?:公开招标|竞争性磋商|竞争性谈判|询比采购|询比|询价|比选|采购|招标)?(?:更正|变更|中标候选人|成交候选人|中标结果|成交结果|中标|成交|结果|废标|终止|重新招标)?(?:公告|公示)(?:\([一二三四五六七八九十\d]+\))?$'
    value = re.sub(suffix, '', value)
    value = re.sub(r'[^\w\u4e00-\u9fff]', '', value).lower()
    buyer = re.sub(r'[^\w\u4e00-\u9fff]', '', text(row.get('buyer', ''))).lower()
    while buyer and value.startswith(buyer):
        value = value[len(buyer):]
    return value


class ProjectHistory:
    """单次回放的项目注册表：保留别名，固定首次公告作为历史样本。"""

    def __init__(self, frame: pd.DataFrame):
        self.number_index = {}
        self.name_index = {}
        self.groups = {}
        self.empty = frame.iloc[:0].copy()
        self.announcement_count = 0
        self.last_order = None
        self._snapshot = None
        self.append(frame)

    def snapshot(self) -> pd.DataFrame:
        """仅在新增项目后重建快照；返回副本，防止调用方修改注册表。"""
        if self._snapshot is None:
            rows = [group['row'] for group in self.groups.values()]
            self._snapshot = pd.DataFrame(rows).reset_index(drop=True) if rows else self.empty.copy()
        return self._snapshot.copy()

    def fork(self) -> ProjectHistory:
        """为当天检测创建独立分支，检测成功后再向正式历史提交。"""
        branch = object.__new__(ProjectHistory)
        branch.number_index = self.number_index.copy()
        branch.name_index = {key: values.copy() for key, values in self.name_index.items()}
        branch.groups = {
            key: {'row': group['row'], 'numbers': group['numbers'].copy()}
            for key, group in self.groups.items()
        }
        branch.empty = self.empty
        branch.announcement_count = self.announcement_count
        branch.last_order = self.last_order
        branch._snapshot = self._snapshot
        return branch

    def append(self, frame: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
        """仅处理新公告，不回扫已归并的历史。"""
        ordered = frame.sort_values(['publish_date', 'id'], kind='stable')
        if not ordered.empty:
            first = (ordered.iloc[0]['publish_date'], ordered.iloc[0]['id'])
            if self.last_order is not None and first <= self.last_order:
                raise ValueError('增量项目归并必须按日期和公告 ID 严格递增')
        annotations = [self._resolve(row) for _, row in ordered.iterrows()]
        self.announcement_count += len(ordered)
        if not ordered.empty:
            last = ordered.iloc[-1]
            self.last_order = (last['publish_date'], last['id'])
        return self.snapshot(), pd.DataFrame(annotations, columns=IDENTITY_COLUMNS)

    def _resolve(self, row: pd.Series) -> dict:
        buyer = text(row.get('buyer', ''))
        number = project_number(row)
        name = project_name(row)
        namespace = 'real'
        buyer_key = (namespace, buyer)

        key = self.number_index.get((buyer_key, number)) if buyer and number else None
        method = '采购单位+项目编号' if key else '独立公告（依据不足）'
        if key is None and buyer and name:
            candidates = self.name_index.get((buyer_key, name), set())
            compatible = [
                candidate for candidate in candidates
                if not number or not self.groups[candidate]['numbers']
                or number in self.groups[candidate]['numbers']
            ]
            if len(compatible) == 1:
                key, method = compatible[0], '采购单位+标准项目名称'

        update = key is not None
        if not update:
            key = str(row['id'])
            self.groups[key] = {'row': row.copy(), 'numbers': set()}
            self._snapshot = None
            if buyer and number:
                method = '采购单位+项目编号'
            elif buyer and name:
                method = '采购单位+标准项目名称'

        # 更新只补充匹配别名，不修改首次日期、金额或语义样本。
        if buyer and number:
            self.number_index[(buyer_key, number)] = key
            self.groups[key]['numbers'].add(number)
        if buyer and name:
            self.name_index.setdefault((buyer_key, name), set()).add(key)
        first = self.groups[key]['row']
        return {
            'id': row['id'],
            'procurement_project_id': key,
            'project_first_seen': first['publish_date'],
            'first_announcement_id': first['id'],
            'project_match_method': method,
            'project_event': 'project_update' if update else 'new_project',
            'is_project_update': update,
        }
