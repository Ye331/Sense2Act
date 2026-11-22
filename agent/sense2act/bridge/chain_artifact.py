"""Worker 的 chain_web.v1 副产物：严格校验、原子写、软降级和补生成。"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from ..chain_web_payload import PROJECTION_VERSION, build_chain_web, validate_chain_web
from ..kb_chain import load_context, open_mains, close_mains
from ..store.ledger import SIMHASH_NEAR_DUP_DIST

STATUS_SCHEMA = "chain_artifact_status.v1"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    try:
        with temp.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass


def _safe_error(exc: Exception) -> tuple[str, str]:
    code = type(exc).__name__
    # 对外状态不泄露路径、traceback 或正文；完整异常由 Worker 日志记录。
    return code, f"证据链出件失败（{code}），可在修复后执行 backfill"


def build_chain_artifact(*, investigation_id: str, event_id: str, ledger,
                         runs_dir: str | Path, config_dir: str | Path) -> dict:
    runs = Path(runs_dir)
    artifact = runs / "chain.web.json"
    status_path = runs / "chain.status.json"
    previous = None
    if status_path.is_file():
        try:
            previous = json.loads(status_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            previous = None
    try:
        context = load_context(config_dir)
        payload = build_chain_web(
            {event_id: ledger}, None, event_id,
            ess_rules=context["ess_rules"], auth_roots=context["auth_roots"],
            near_dup_dist=SIMHASH_NEAR_DUP_DIST,
            seeds_path=str(Path(config_dir) / "hypothesis_seeds.json"),
            config_dir=config_dir)
        validate_chain_web(payload, strict=True)
        _atomic_json(artifact, payload)
        web = payload.get("meta", {}).get("web", {})
        status = {
            "schema_version": STATUS_SCHEMA,
            "investigation_id": investigation_id,
            "event_id": event_id,
            "status": "ready",
            "generated_at": payload.get("meta", {}).get("generated_at") or _now(),
            "projection_version": payload.get("meta", {}).get("projection_version") or PROJECTION_VERSION,
            "n_nodes": int(web.get("n_nodes", len(payload.get("nodes", [])))),
            "n_edges": int(web.get("n_edges", len(payload.get("edges", [])))),
            "error_code": None,
            "error_message": None,
        }
        _atomic_json(status_path, status)
        return status
    except Exception as exc:
        code, message = _safe_error(exc)
        status = {
            "schema_version": STATUS_SCHEMA,
            "investigation_id": investigation_id,
            "event_id": event_id,
            "status": "failed",
            "generated_at": _now(),
            "projection_version": PROJECTION_VERSION,
            "n_nodes": 0,
            "n_edges": 0,
            "error_code": code,
            "error_message": message,
        }
        if artifact.is_file():
            status["last_good_generated_at"] = (
                (previous or {}).get("generated_at") or
                datetime.fromtimestamp(artifact.stat().st_mtime, timezone.utc).isoformat(timespec="seconds"))
        _atomic_json(status_path, status)
        raise


def backfill(data_dir: str | Path, config_dir: str | Path, *,
             investigation: str = "", all_missing: bool = False) -> list[dict]:
    root = Path(data_dir)
    targets = [root / investigation] if investigation else sorted(
        path for path in root.iterdir() if path.is_dir())
    results: list[dict] = []
    for workdir in targets:
        db = workdir / "ledger.db"
        if not db.is_file():
            continue
        status_path = workdir / "runs" / "chain.status.json"
        if all_missing and status_path.is_file():
            try:
                if json.loads(status_path.read_text(encoding="utf-8")).get("status") == "ready":
                    continue
            except (OSError, ValueError):
                pass
        mains = open_mains([db], config_dir)
        try:
            if len(mains) != 1:
                raise RuntimeError(f"{db} 必须且只能包含一个 event")
            event_id, ledger = next(iter(mains.items()))
            results.append(build_chain_artifact(
                investigation_id=workdir.name, event_id=event_id, ledger=ledger,
                runs_dir=workdir / "runs", config_dir=config_dir))
        finally:
            close_mains(mains)
    return results
