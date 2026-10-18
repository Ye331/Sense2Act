"""第 2 档接入件:ContentItem 映射 / CrawlerGate 录制-回放往返 / 指纹确定性。"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from sense2act.collect import (  # noqa: E402
    CrawlerGate,
    NotRecordedError,
    contentitem_to_lite,
    request_fingerprint,
)
from sense2act.models.observation import CollectRequest  # noqa: E402


CONTENT_ITEM = {
    "schemaVersion": "content.v1",
    "contentId": "c_001",
    "platform": "toutiao",
    "sourceType": "news",
    "contentType": "article",
    "title": "某医院启动AI采购",
    "content": "正文内容:该项目预算480万元。",
    "url": "https://www.toutiao.com/article/123",
    "author": {"name": "记者甲", "verified": True},
    "publishedAt": "2026-09-20T10:00:00",
    "dedup": {"dedupKey": "dk_123", "contentHash": "h"},
    "quality": {"hasFullContent": True},
}


class TestContentItemMapping(unittest.TestCase):
    def test_full_mapping(self):
        lite = contentitem_to_lite(CONTENT_ITEM)
        self.assertEqual(lite.platform, "toutiao")
        self.assertEqual(lite.source, "www.toutiao.com")   # 域名 -> 谱系根原料
        self.assertEqual(lite.dedup_key, "dk_123")
        self.assertEqual(lite.author_type, "认证账号")
        self.assertIn("480", lite.content)

    def test_fallbacks(self):
        lite = contentitem_to_lite({
            "platform": "sogou_wechat", "contentId": "c_9",
            "summary": "只有摘要", "url": ""})
        self.assertEqual(lite.source, "sogou_wechat")       # 无 URL -> 平台名
        self.assertEqual(lite.content, "只有摘要")
        self.assertEqual(lite.dedup_key, "c_9")


class StubClient:
    """记录调用的假客户端(验证 replay 零网络)。"""

    def __init__(self, receipts):
        self._receipts = list(receipts)
        self.calls = 0

    def search(self, request):
        self.calls += 1
        return self._receipts.pop(0)


def make_receipt(query):
    from sense2act.models.observation import CollectReceipt, EvidenceItemLite
    return CollectReceipt(
        request=CollectRequest(query=query, platforms=["toutiao"], limit=10),
        items=[EvidenceItemLite(
            platform="toutiao", source="www.toutiao.com", title="t",
            content=f"{query} 的正文")],
        status="success")


class TestCrawlerGate(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def test_record_then_replay_roundtrip(self):
        """录制 -> 回放:同指纹同响应;回放模式零网络(客户端零调用)。"""
        rec_path = Path(self.tmp) / "rec.json"
        stub = StubClient([make_receipt("医院 AI采购"), make_receipt("第二问")])
        gate = CrawlerGate(stub, record_path=rec_path)
        r1 = gate.send(CollectRequest(query="医院 AI采购", platforms=["toutiao"]))
        r2 = gate.send(CollectRequest(query="第二问", platforms=["toutiao"]))
        gate.save()
        self.assertEqual(stub.calls, 2)

        replay = CrawlerGate(replay_path=rec_path)
        p1 = replay.send(CollectRequest(query="医院 AI采购", platforms=["toutiao"]))
        p2 = replay.send(CollectRequest(query="第二问", platforms=["toutiao"]))
        self.assertEqual(p1.items[0].content, r1.items[0].content)
        self.assertEqual(p2.items[0].content, r2.items[0].content)
        self.assertEqual(p1.items[0].content, "医院 AI采购 的正文")  # 字节级同响应

    def test_replay_miss_raises(self):
        rec_path = Path(self.tmp) / "rec.json"
        gate = CrawlerGate(StubClient([make_receipt("q1")]), record_path=rec_path)
        gate.send(CollectRequest(query="q1"))
        gate.save()
        replay = CrawlerGate(replay_path=rec_path)
        with self.assertRaises(NotRecordedError):
            replay.send(CollectRequest(query="从没录过的查询"))

    def test_fingerprint_stable_across_key_order(self):
        a = CollectRequest(query="q", platforms=["toutiao", "cctv_news"], limit=10)
        b = CollectRequest(query="q", platforms=["cctv_news", "toutiao"], limit=10)
        self.assertEqual(request_fingerprint(a), request_fingerprint(b))
        c = a.model_copy(update={"query": "q2"})
        self.assertNotEqual(request_fingerprint(a), request_fingerprint(c))

    def test_record_file_writes_incrementally(self):
        """I3 录制完整性:边跑边写(崩溃不丢已录部分)。"""
        rec_path = Path(self.tmp) / "rec.json"
        gate = CrawlerGate(StubClient([make_receipt("q")]), record_path=rec_path)
        gate.send(CollectRequest(query="q"))
        data = json.loads(rec_path.read_text(encoding="utf-8"))
        self.assertEqual(len(data), 1)


if __name__ == "__main__":
    unittest.main()
