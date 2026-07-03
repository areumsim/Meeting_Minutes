"""
supermemory_client.py — Supermemory 연동 래퍼
=============================================
회의록 저장 시점에 Supermemory에도 동시 저장, 다음 회의 컨텍스트 빌딩 시 자동 조회.

설정 (config.json):
  "supermemory": {
    "enabled": false,
    "api_key": "",
    "base_url": "https://api.supermemory.ai"
  }

자체 호스팅 (MIT 라이선스, 데이터 로컬 보관):
  npx supermemory local   → http://localhost:6767 에서 동작
  base_url 을 "http://localhost:6767" 으로 설정
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

try:
    import config_loader as _cfg
    _cfg_ok = True
except ImportError:
    _cfg = None  # type: ignore
    _cfg_ok = False


def _c(key: str, default: Any = None) -> Any:
    return _cfg.get(key, default) if _cfg_ok else default


class SupermemoryClient:
    """Supermemory API 래퍼. enabled=false 또는 SDK 미설치 시 모든 메서드 no-op."""

    def __init__(self) -> None:
        self._client: Any = None
        self._ready = False

        if not _c("supermemory.enabled", False):
            return

        api_key = _c("supermemory.api_key", "") or ""
        base_url = _c("supermemory.base_url", "https://api.supermemory.ai") or ""

        try:
            from supermemory import Supermemory  # type: ignore
            kwargs: Dict[str, Any] = {}
            if api_key:
                kwargs["api_key"] = api_key
            if base_url:
                kwargs["base_url"] = base_url
            self._client = Supermemory(**kwargs)
            self._ready = True
            logger.info("Supermemory 연결됨 (%s)", base_url or "cloud")
        except ImportError:
            logger.warning("supermemory SDK 미설치 — pip install supermemory")
        except Exception as exc:
            logger.warning("Supermemory 초기화 실패: %s", exc)

    def enabled(self) -> bool:
        return self._ready

    def save(self, text: str, *, metadata: Optional[Dict[str, Any]] = None, container_tag: str = "") -> None:
        """텍스트를 Supermemory에 저장. 실패해도 파이프라인 중단 없음."""
        if not self._ready or not text.strip():
            return
        try:
            kwargs: Dict[str, Any] = {"content": text}
            if container_tag:
                kwargs["container_tag"] = container_tag
            if metadata:
                kwargs["metadata"] = {k: v for k, v in metadata.items() if v not in (None, "", [])}
            self._client.add(**kwargs)
        except Exception as exc:
            logger.warning("Supermemory 저장 실패 (무시): %s", exc)

    def search(self, query: str, *, container_tag: str = "", limit: int = 5) -> List[str]:
        """관련 메모리 텍스트 조각 목록 반환. 실패 시 빈 리스트."""
        if not self._ready or not query.strip():
            return []
        try:
            kwargs: Dict[str, Any] = {"q": query, "limit": limit, "search_mode": "memories"}
            if container_tag:
                kwargs["container_tag"] = container_tag
            result = self._client.search(**kwargs)
            items: List[str] = []
            for item in (result or []):
                if isinstance(item, str):
                    items.append(item)
                elif hasattr(item, "content"):
                    items.append(str(item.content))
                elif isinstance(item, dict):
                    items.append(str(item.get("content") or item.get("text") or ""))
            return [i for i in items if i.strip()]
        except Exception as exc:
            logger.debug("Supermemory 검색 실패 (무시): %s", exc)
            return []


_instance: Optional[SupermemoryClient] = None


def get_client() -> SupermemoryClient:
    """모듈 레벨 싱글턴 — 최초 호출 시 1회 초기화."""
    global _instance
    if _instance is None:
        _instance = SupermemoryClient()
    return _instance
