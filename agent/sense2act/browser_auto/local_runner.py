"""本地 playwright 执行器(决策 #14 修订的落点,2026-10-23)。

原执行器(runner.py)经 WSL 调原作者 F 盘脚本,本机不存在——本执行器以
本机 playwright 替代其落点,通道纪律全额保留:默认 dry-run(只打印,
不真拍、不消耗硬限);显式 --no-dry-run 才真访问;硬限协议在调用方
CaptureGate 强制,不靠本执行器自觉。playwright 惰性导入:未安装的环境
(离线/CI)不受影响,导入失败给可操作的提示而非崩栈。

有头模式(2026-10-23 实测):ccgp/天津政采对 headless 指纹一律拦截
(403/"系统限制"页),有头真浏览器正常放行——决策 #14 本意即真浏览器
捕获,故默认 headless=False(真跑时会短暂弹出浏览器窗口,透明可见);
拦截/异常页如实放弃返回空,绝不把 403 截图当附件、不做指纹伪装对抗。

聚焦三代(2026-10-24,用户反馈:整页仍出现/关键图只有标签没内容):
- 正文图:已知容器(#noticeArea 等)→ JS「最小大文本块」兜底(≥400 字的
  最小元素=正文区,天津 #content 类 div 布局通用)→ 都失手才整页(如实标注);
- 关键行:JS 求「含词且文本量适中(词+4~300 字)的最小区块」——表格页落到
  tr(标签+值同行),纯 div 布局无小区块可落时改为**标签横向裁带**
  (标签下 +180px,值必在带内),杜绝只拍标签格或整页巨图。
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger("browser_auto")

_TIMEOUT_S = 30   # 页面加载上限(networkidle 对重脚本站可能慢,给足但不无限)
_BLOCK_TITLE_MARKERS = ("403", "forbidden", "访问异常", "频繁访问",
                        "系统限制", "安全验证", "验证码")
# 已知正文容器(ccgp 详情页;与 crawler/adapters/ccgp.py parse_detail_body 同源)
_BODY_SELECTOR = "#noticeArea, .vF_detail_content"
_SAFE_NAME_RE = re.compile(r"[^\w一-鿿-]+")

# JS:最小大文本块(≥400 字的最小元素≈正文区;嵌套包装层文本相同取最内层)
_JS_DENSE_BLOCK = """
() => {
  let best = null;
  for (const el of document.querySelectorAll('div,article,section')) {
    const t = (el.innerText || '');
    if (t.length < 400 || el.clientWidth < 300) continue;
    if (!best || t.length < best.len) best = {el: el, len: t.length};
  }
  return best ? best.el : null;
}
"""

# JS:含词的最小区块(词+4~300 字)与词叶子的矩形;区块不可得时供裁带用
_JS_KEY_BLOCK = """
(pattern) => {
  let leaf = null;
  for (const el of document.querySelectorAll('body *')) {
    const t = (el.innerText || '');
    if (!t.includes(pattern)) continue;
    if (el.children.length === 0 || t.length <= 300) {
      if (!leaf || t.length < leaf.t) leaf = {el: el, t: t.length};
    }
  }
  if (!leaf) return null;
  let node = leaf.el, pick = null;
  while (node && node !== document.body) {
    const t = node.innerText || '';
    if (t.includes(pattern)) {
      if (t.length >= pattern.length + 4 && t.length <= 300) pick = node;
      break;                      // 首个含词祖先即判:够格用它,不够格转裁带
    }
    node = node.parentElement;
  }
  const r = leaf.el.getBoundingClientRect();
  return {pick: pick, label: {x: r.x, y: r.y, w: r.width, h: r.height},
          pageH: document.documentElement.scrollHeight};
}
"""


@dataclass
class CaptureResult:
    """一次访问的截图产物;失败如实为空,不伪装。"""

    full: Path | None = None                  # 正文区截图(兜底到整页时如实标记)
    used_fallback_page: bool = False          # 正文容器与文本块均未命中,退了整页
    keys: list[tuple[str, Path]] = field(default_factory=list)   # (定位词, 关键行图)


def _safe_name(label: str) -> str:
    return _SAFE_NAME_RE.sub("", label)[:24] or "key"


class LocalPlaywrightRunner:
    """Chromium 截图执行器(browser_auto 隔离通道,B 线;默认有头真浏览器)。"""

    def __init__(self, *, dry_run: bool = True, headless: bool = False,
                 timeout_s: int = _TIMEOUT_S):
        self.dry_run = dry_run
        self.headless = headless
        self.timeout_s = timeout_s

    def capture(self, url: str, out_path: str | Path,
                key_patterns: tuple[str, ...] = ()) -> CaptureResult:
        """一次访问:正文区截图 + 关键行小图;dry-run 只打印计划。"""
        out_path = Path(out_path)
        result = CaptureResult()
        if self.dry_run:
            plan = f"正文区({out_path.name})"
            plan += "".join(f" + 关键行[{p}]" for p in key_patterns)
            print(f"[browser_auto:dry-run] 将截图: {url} -> {plan}")
            return result
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise RuntimeError(
                "未安装 playwright(截图通道不可用): pip install playwright"
                " && playwright install chromium") from exc
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=self.headless)
                try:
                    page = browser.new_page()
                    resp = page.goto(url, wait_until="networkidle",
                                     timeout=self.timeout_s * 1000)
                    if resp is not None and resp.status >= 400:
                        log.warning("截图目标返回 HTTP %s(不对抗,放弃): %s",
                                    resp.status, url)
                        return result
                    title = (page.title() or "").lower()
                    if any(m in title for m in _BLOCK_TITLE_MARKERS):
                        log.warning("截图目标渲染为拦截/限制页(%s),放弃: %s",
                                    page.title(), url)
                        return result
                    out_path.parent.mkdir(parents=True, exist_ok=True)
                    stem = out_path.with_suffix("").name
                    # ① 正文区:已知容器 -> 最小大文本块 -> 整页(如实标记)
                    body = self._first_visible(page, _BODY_SELECTOR)
                    if body is None:
                        body = self._as_element(
                            page.evaluate_handle(_JS_DENSE_BLOCK))
                    if body is not None:
                        body.screenshot(path=str(out_path))
                        result.full = out_path
                    else:
                        page.screenshot(full_page=True, path=str(out_path))
                        result.full = out_path
                        result.used_fallback_page = True
                    # ② 关键行:最小区块元素图;无小区块 -> 标签横向裁带
                    for pattern in key_patterns:
                        kp = out_path.with_name(
                            f"{stem}_key_{_safe_name(pattern)}.png")
                        if self._shoot_key(page, pattern, kp):
                            result.keys.append((pattern, kp))
                finally:
                    browser.close()
        except Exception as exc:  # noqa: BLE001 —— 失败如实返回空,不伪装
            log.warning("截图失败 %s: %s", url, exc)
            return CaptureResult()
        return result

    # ------------------------------------------------------------------

    def _first_visible(self, page, selector: str):
        loc = page.locator(selector).first
        try:
            loc.wait_for(state="visible", timeout=4000)
            return loc
        except Exception:  # noqa: BLE001 —— 容器缺失走兜底
            return None

    @staticmethod
    def _as_element(handle):
        el = handle.as_element() if handle else None
        try:
            if el is not None and el.is_visible():
                return el
        except Exception:  # noqa: BLE001
            return None
        return None

    def _shoot_key(self, page, pattern: str, out: Path) -> bool:
        """关键行:含词最小区块截图;纯 div 布局无小区块 -> 标签横向裁带。"""
        try:
            info = page.evaluate(_JS_KEY_BLOCK, pattern)
        except Exception:  # noqa: BLE001
            info = None
        if not info:
            log.info("关键行未命中『%s』,跳过", pattern)
            return False
        try:
            pick = self._as_element(info.get("pick"))
            if pick is not None:
                pick.scroll_into_view_if_needed(timeout=3000)
                pick.screenshot(path=str(out))
                return True
        except Exception:  # noqa: BLE001 —— 区块截图失败转裁带
            pass
        # 裁带:标签上 24px ~ 下 +180px,全宽;值必在带内(标签格/小节头场景)
        try:
            label = info["label"]
            page.evaluate(
                "y => window.scrollTo(0, Math.max(0, y - 120))", label["y"])
            page.wait_for_timeout(400)
            label = page.evaluate(  # 滚动后重取视口坐标
                """p => { const el = [...document.querySelectorAll('body *')]
                     .filter(e => e.children.length === 0 &&
                                  (e.innerText || '').includes(p))[0];
                     if (!el) return null;
                     el.scrollIntoView({block: 'center'});
                     const r = el.getBoundingClientRect();
                     return {x: r.x, y: r.y, w: r.width, h: r.height}; }""",
                pattern)
            if not label:
                return False
            vy = page.evaluate("() => window.scrollY")
            band_h = int(label["h"]) + 204
            page.screenshot(
                full_page=True,          # clip 按文档坐标语义裁全页
                clip={"x": 0, "y": vy + int(label["y"]) - 24,
                      "width": page.viewport_size["width"],
                      "height": band_h},
                path=str(out))
            return True
        except Exception as exc:  # noqa: BLE001 —— 裁带也失败=如实放弃
            log.info("关键行『%s』裁带失败: %s", pattern, exc)
            return False

    # 兼容旧调用(capture_cli/测试替身沿用单图语义)
    def screenshot(self, url: str, out_path: str | Path) -> Path | None:
        return self.capture(url, out_path).full
