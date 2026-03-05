"""
profiles.py – Named Profile 시스템
====================================
"주간회의", "세미나_영어", "강의녹취" 등 용도별 프리셋을
하나의 profiles.json에 관리합니다.

사용 예:
    python meeting_minutes.py input.mp4 --profile weekly
    python profiles.py list               # 목록 보기
    python profiles.py create             # 대화형 생성
    python profiles.py show weekly_team   # 상세 보기
"""

from __future__ import annotations

import json
import os
import copy
from typing import Any, Optional


# ── 기본 내장 프로필 ──────────────────────────────────────────
BUILTIN_PROFILES: dict[str, dict[str, Any]] = {
    "meeting_ko": {
        "description": "한국어 정기회의 (기본) — 화자분리 포함",
        "type": "meeting",
        "language": "ko",
        "llm": "gpt",
        "model": "gpt-4o-transcribe-diarize",  # 화자분리 지원 모델
        "translate": False,
    },
    "meeting_en2ko": {
        "description": "영어회의 → 한국어 문서 — 화자분리 포함",
        "type": "meeting",
        "language": "en",
        "llm": "gpt",
        "model": "gpt-4o-transcribe-diarize",  # 화자분리 지원 모델
        "translate": True,
        "translate_script": True,
    },
    "seminar": {
        "description": "영어 세미나 → 한국어 세미나 기록 — 화자분리 포함",
        "type": "seminar",
        "language": "en",
        "translate": True,
        "llm": "gpt",
        "model": "gpt-4o-transcribe-diarize",
    },
    "lecture": {
        "description": "영어 강의 → 한국어 강의노트",
        "type": "lecture",
        "language": "en",
        "translate": True,
        "llm": "gpt",
        "model": "gpt-4o-transcribe",  # 강의는 단일 화자이므로 diarize 불필요
    },
}


class ProfileManager:
    DEFAULT_PATH = "profiles.json"

    def __init__(self, path: str = DEFAULT_PATH):
        self.path = path
        self._profiles: dict[str, dict] = self._load()

    # ── 프로필 CRUD ───────────────────────────────────────

    def create_profile(self, name: str, config: dict, overwrite: bool = False) -> None:
        if name in self._profiles and not overwrite:
            raise ValueError(f"프로필 '{name}' 이미 존재합니다. overwrite=True로 덮어쓰기")
        self._profiles[name] = {
            **config,
            "_meta": {"created_by": "user"},
        }
        self._save()

    def get_profile(self, name: str) -> Optional[dict]:
        """사용자 프로필 우선, 없으면 빌트인에서 검색."""
        if name in self._profiles:
            return {k: v for k, v in self._profiles[name].items() if k != "_meta"}
        if name in BUILTIN_PROFILES:
            return dict(BUILTIN_PROFILES[name])
        return None

    def list_profiles(self) -> list[tuple[str, str, str]]:
        """(이름, 설명, 출처) 리스트."""
        result = []
        for name, cfg in BUILTIN_PROFILES.items():
            overridden = " (사용자 재정의)" if name in self._profiles else ""
            result.append((name, cfg.get("description", ""), f"builtin{overridden}"))
        for name, cfg in self._profiles.items():
            if name not in BUILTIN_PROFILES:
                result.append((name, cfg.get("description", ""), "user"))
        return result

    def delete_profile(self, name: str) -> bool:
        if name in self._profiles:
            del self._profiles[name]
            self._save()
            return True
        return False

    # ── argparse 통합 ─────────────────────────────────────

    def apply_profile(self, name: str, args: Any) -> Any:
        """
        argparse Namespace에 프로필 값을 병합합니다.
        CLI에서 명시적으로 지정한 값은 덮어쓰지 않습니다.
        우선순위: CLI > 프로필 > 기본값
        """
        profile = self.get_profile(name)
        if profile is None:
            available = ", ".join(n for n, _, _ in self.list_profiles())
            raise ValueError(
                f"프로필 '{name}'을 찾을 수 없습니다.\n"
                f"사용 가능: {available}\n"
                f"  python profiles.py list 로 전체 목록 확인"
            )

        args_copy = copy.copy(args)
        for key, value in profile.items():
            if key in ("description", "_meta"):
                continue
            current = getattr(args_copy, key, None)
            # CLI 기본값(None, False)인 경우에만 프로필 값 적용
            if current is None or current is False:
                setattr(args_copy, key, value)

        return args_copy

    # ── 대화형 프로필 생성 ────────────────────────────────

    def interactive_create(self) -> str:
        """대화형으로 새 프로필을 만듭니다."""
        print("\n  새 프로필 만들기\n")
        name = input("  프로필 이름 (영문, 예: weekly_team): ").strip()
        if not name:
            raise ValueError("이름을 입력해주세요")

        config: dict[str, Any] = {}
        config["description"] = input("  설명: ").strip() or name

        type_choice = input("  문서 타입 (meeting/seminar/lecture) [meeting]: ").strip()
        config["type"] = type_choice if type_choice in ("meeting", "seminar", "lecture") else "meeting"

        lang = input("  언어 (ko/en/auto) [ko]: ").strip()
        config["language"] = lang if lang in ("ko", "en", "auto") else "ko"

        if config["language"] == "en":
            tr = input("  영→한 번역? (y/N): ").strip().lower()
            config["translate"] = tr in ("y", "yes")

        llm = input("  LLM (gpt/claude) [gpt]: ").strip()
        config["llm"] = llm if llm in ("gpt", "claude") else "gpt"

        model = input("  STT 모델 [gpt-4o-mini-transcribe]: ").strip()
        config["model"] = model or "gpt-4o-mini-transcribe"

        speakers = input("  고정 화자 (쉼표구분, Enter=자동): ").strip()
        if speakers:
            config["speakers"] = speakers

        custom = input("  LLM 추가 지시 (Enter=없음): ").strip()
        if custom:
            config["custom_prompt"] = custom

        notify = input("  완료 알림 (email/slack/teams/없음) [없음]: ").strip().lower()
        if notify in ("email", "slack", "teams"):
            config["notify"] = notify

        self.create_profile(name, config, overwrite=True)
        print(f"\n  프로필 [{name}] 저장됨")
        return name

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
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(self._profiles, f, ensure_ascii=False, indent=2)


# ── CLI 단독 실행 ─────────────────────────────────────────
if __name__ == "__main__":
    import sys
    pm = ProfileManager()

    cmd = sys.argv[1] if len(sys.argv) > 1 else "list"

    if cmd == "list":
        print("\n  사용 가능한 프로필:\n")
        for name, desc, source in pm.list_profiles():
            tag = f"[{source}]"
            print(f"  {name:<22s} {desc:<32s} {tag}")
        print(f"\n  사용: python meeting_minutes.py input.mp4 --profile <이름>")

    elif cmd == "create":
        pm.interactive_create()

    elif cmd == "show" and len(sys.argv) > 2:
        profile = pm.get_profile(sys.argv[2])
        if profile:
            print(json.dumps(profile, ensure_ascii=False, indent=2))
        else:
            print(f"프로필 '{sys.argv[2]}'을 찾을 수 없습니다.")

    elif cmd == "delete" and len(sys.argv) > 2:
        if pm.delete_profile(sys.argv[2]):
            print(f"프로필 '{sys.argv[2]}' 삭제됨")
        else:
            print(f"프로필 '{sys.argv[2]}'을 찾을 수 없습니다.")

    else:
        print("사용법:")
        print("  python profiles.py list              # 프로필 목록")
        print("  python profiles.py create            # 대화형 생성")
        print("  python profiles.py show <이름>       # 프로필 상세")
        print("  python profiles.py delete <이름>     # 프로필 삭제")
