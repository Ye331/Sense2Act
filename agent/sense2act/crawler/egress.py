"""唯一出网点(EgressHttpClient)+ 真出网闸门(P0 落在正确的边界上)。

爬虫框架自建后,录制边界从"Sense2act↔外部服务"前移到"适配器↔网站"的
真实出网点——这才是架构决策 #15 的原意(I1 唯一出口:任何网络访问必经此处,
绕过即缺陷;I2 replay 零网络;I3 成败都录,边跑边写;I4 同 URL 字节级同响应)。

适配器不持任何 HTTP 能力,只拿一个 http_get 可调用(依赖注入,
与 F 盘 crawler_tool 的 bind_http_get 同构)。
"""
from __future__ import annotations

import hashlib
import json
import urllib.request
from pathlib import Path


def egress_fingerprint(url: str) -> str:
    return hashlib.sha256(f"GET {url}".encode("utf-8")).hexdigest()[:16]


class EgressResponse:
    def __init__(self, status_code: int | None, text: str):
        self.status_code = status_code
        self.text = text


class EgressHttpClient:
    """http_get 的标准实现;transport 可注入(测试替身)。"""

    def __init__(self, *, record_path: str | Path | None = None,
                 replay_path: str | Path | None = None,
                 timeout: float = 10.0, transport=None):
        if record_path and replay_path:
            raise ValueError("record 与 replay 互斥")
        if replay_path and transport is not None:
            raise ValueError("replay 零网络:不接受 transport(I2)")
        self._record_path = Path(record_path) if record_path else None
        self._replay_path = Path(replay_path) if replay_path else None
        self._timeout = timeout
        self._transport = transport
        self.calls = 0
        self._pending: dict[str, dict] = {}
        self._records: dict[str, dict] = {}
        self._memo: dict[str, EgressResponse] = {}   # 单次运行内同 URL 只取一次网
        if self._replay_path:
            if not self._replay_path.exists():
                raise FileNotFoundError(f"出网回放文件不存在: {self._replay_path}")
            self._records = json.loads(
                self._replay_path.read_text(encoding="utf-8-sig")).get("records", {})

    def get(self, url: str, timeout: float | None = None) -> EgressResponse:
        # 运行内记忆:同 URL 秒回(探索器换词在单源适配器上会反复打到同一列表,
        # 不缓存则同一 URL 被真实 GET 十几次——浪费预算且对源不礼貌)
        memo = self._memo.get(url)
        if memo is not None:
            return memo
        fp = egress_fingerprint(url)
        self.calls += 1
        if self._records:
            rec = self._records.get(fp)
            if rec is None:
                from ..collect.crawler_gate import NotRecordedError
                raise NotRecordedError(
                    f"出网回放未命中 {fp}({url})——请在录制模式重跑")
            resp = EgressResponse(rec["status_code"], rec["text"])
            self._memo[url] = resp
            return resp
        status_code, text = self._fetch(url, timeout or self._timeout)
        resp = EgressResponse(status_code, text)
        self._memo[url] = resp
        if self._record_path is not None:
            self._pending[fp] = {"url": url, "status_code": status_code,
                                 "text": text}
            self._flush()
        return resp

    def _fetch(self, url: str, timeout: float) -> tuple[int | None, str]:
        if self._transport is not None:
            status_code, text = self._transport(url, timeout)
            return status_code, text
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0",
            # 声明不压缩:乙方宝等站对无 Accept-Encoding 的客户端会主动 gzip,
            # egress 存解码后文本,压缩体会变乱码(误报 parser_changed)
            "Accept-Encoding": "identity",
        })
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.status, self._decode_body(resp.read())
        except urllib.error.HTTPError as exc:
            return exc.code, ""
        except Exception as exc:
            raise TimeoutError(type(exc).__name__) from exc

    @staticmethod
    def _decode_body(data: bytes) -> str:
        """服务器无视 identity 仍返回 gzip 时按魔法数兜底解压(字节级确定性)。"""
        if data[:2] == b"\x1f\x8b":
            import gzip
            data = gzip.decompress(data)
        return data.decode("utf-8", errors="replace")

    def _flush(self) -> None:
        if self._record_path is None:
            return
        self._record_path.parent.mkdir(parents=True, exist_ok=True)
        self._record_path.write_text(
            json.dumps({"kind": "egress_v1", "records": self._pending},
                       ensure_ascii=False, indent=1),
            encoding="utf-8")

    def save(self) -> None:
        self._flush()
