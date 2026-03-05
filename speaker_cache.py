"""
speaker_cache.py – 화자 매핑 캐시
=================================
같은 팀 정기회의에서 매번 화자명을 다시 입력하지 않도록
이전 매핑을 저장·재사용합니다.

사용 예:
    from speaker_cache import SpeakerCache
    cache = SpeakerCache()
    mapping = cache.interactive_edit(segments, title="주간회의")

    python speaker_cache.py list        # 저장된 매핑 목록
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Optional


class SpeakerCache:
    """화자 이름 매핑을 프로젝트/회의 단위로 캐싱합니다."""

    DEFAULT_PATH = os.path.join("output", "speaker_map.json")

    def __init__(self, path: str = DEFAULT_PATH):
        self.path = path
        self._data: dict = self._load()

    # ── 공개 API ──────────────────────────────────────────

    def save_mapping(self, key: str, mapping: dict[str, str]) -> None:
        """
        매핑 저장.
        key: 프로젝트/회의 식별자 (예: "주간회의", "세미나_AI")
        mapping: {"Speaker 1": "김팀장", "Speaker 2": "이대리"}
        """
        self._data[key] = {
            "mapping": mapping,
            "updated_at": datetime.now().isoformat(),
            "use_count": self._data.get(key, {}).get("use_count", 0),
        }
        self._save()

    def get_mapping(self, key: str) -> Optional[dict[str, str]]:
        """저장된 매핑 반환. 없으면 None."""
        entry = self._data.get(key)
        if entry is None:
            return None
        entry["use_count"] = entry.get("use_count", 0) + 1
        entry["last_used"] = datetime.now().isoformat()
        self._save()
        return entry["mapping"]

    def list_keys(self) -> list[str]:
        """저장된 모든 매핑 키 목록."""
        return sorted(self._data.keys())

    def fuzzy_match(self, title: str) -> Optional[str]:
        """
        제목에 포함된 키워드로 가장 최근 매핑을 자동 매칭.
        예: title="2025 Q2 주간회의" → key="주간회의" 자동 매칭
        """
        if not title:
            return None
        candidates = []
        for key in self._data:
            if key in title or title in key:
                candidates.append((key, self._data[key].get("updated_at", "")))
        if not candidates:
            return None
        candidates.sort(key=lambda x: x[1], reverse=True)
        return candidates[0][0]

    def delete_mapping(self, key: str) -> bool:
        if key in self._data:
            del self._data[key]
            self._save()
            return True
        return False

    # ── 대화형 화자 수정 ──────────────────────────────────

    def interactive_edit(self, segments: list[dict], title: str = "") -> dict[str, str]:
        """
        세그먼트에서 화자 목록 추출 → 캐시 확인 → 대화형 수정 → 저장.
        기존 --edit-speakers 흐름에 캐시 기능을 추가합니다.
        """
        speakers = sorted({s.get("speaker", "") for s in segments if s.get("speaker")})
        if not speakers:
            return {}

        # 캐시에서 자동 매칭 시도
        cached_key = self.fuzzy_match(title) if title else None
        cached = self.get_mapping(cached_key) if cached_key else None

        mapping: dict[str, str] = {}
        if cached:
            print(f"\n  이전 매핑 발견: [{cached_key}]")
            for orig, name in cached.items():
                print(f"     {orig} → {name}")

            reuse = input("\n  이 매핑을 재사용할까요? (Y/n/edit): ").strip().lower()
            if reuse in ("", "y", "yes"):
                return cached
            elif reuse == "edit":
                mapping = dict(cached)

        # 대화형 입력
        print(f"\n  화자 이름 설정 (Enter로 건너뛰기):")
        for speaker in speakers:
            current = mapping.get(speaker, "")
            prompt = f"     {speaker}"
            if current:
                prompt += f" [{current}]"
            prompt += ": "
            new_name = input(prompt).strip()
            if new_name:
                mapping[speaker] = new_name
            elif current:
                mapping[speaker] = current

        # 저장
        if mapping:
            save_key = title or input("  이 매핑을 저장할 이름 (Enter=건너뛰기): ").strip()
            if save_key:
                self.save_mapping(save_key, mapping)
                print(f"     [{save_key}]로 저장됨")

        return mapping

    # ── 내부 ──────────────────────────────────────────────

    def _load(self) -> dict:
        if os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError):
                return {}
        return {}

    def _save(self) -> None:
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(self._data, f, ensure_ascii=False, indent=2)


# ── CLI 단독 실행 ─────────────────────────────────────────
if __name__ == "__main__":
    import sys
    cache = SpeakerCache()

    cmd = sys.argv[1] if len(sys.argv) > 1 else "list"

    if cmd == "list":
        keys = cache.list_keys()
        if not keys:
            print("저장된 화자 매핑이 없습니다.")
        else:
            print(f"\n  저장된 화자 매핑 ({len(keys)}개):\n")
            for k in keys:
                entry = cache._data[k]
                mapping = entry.get("mapping", {})
                print(f"  [{k}]  {len(mapping)}명  사용 {entry.get('use_count', 0)}회  "
                      f"수정 {entry.get('updated_at', '?')[:10]}")
                for orig, name in mapping.items():
                    print(f"     {orig} → {name}")

    elif cmd == "delete" and len(sys.argv) > 2:
        if cache.delete_mapping(sys.argv[2]):
            print(f"매핑 '{sys.argv[2]}' 삭제됨")
        else:
            print(f"매핑 '{sys.argv[2]}'을 찾을 수 없습니다.")

    else:
        print("사용법:")
        print("  python speaker_cache.py list             # 저장된 매핑 목록")
        print("  python speaker_cache.py delete <이름>   # 매핑 삭제")
