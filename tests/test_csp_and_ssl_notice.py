"""CSP 빌드 프로파일(SEC-006)과 SSL 검증 꺼짐 고지.

**CSP** — 하나의 CSP 로 두 배포 형태를 덮으려니 `connect-src` 에 `http: https: ws: wss:`
가 들어가 **임의 호스트**가 허용됐다. 두 형태의 요구가 실제로 다르다.

  packaged   PC 배포본. FastAPI 가 같은 오리진에서 프런트를 서빙하고 외부 호출은 백엔드를
             지난다 → `'self'` 로 충분하다(같은 오리진의 ws/wss 포함).
  standalone 아이폰 앱 번들. 서버 없이 OpenAI 를 직접 부르고 사용자가 입력한 LAN 주소에
             붙는다 → 그 주소를 빌드 시점에 알 수 없다.

dist/ 는 빌드 산출물이라 커밋하지 않으므로, 여기서는 **설정 소스**를 검사한다
(빌드 결과 검증은 `npm run build` 로 실제 확인했다).

**SSL** — `ssl.verify=false` 는 기본값이 아니라 사용자가 켠 탈출구다. truststore 로
Windows 인증서 저장소를 신뢰하므로 대개 불필요한데, 켜 둔 채 잊으면 API 키와 회의 내용이
검증 없는 TLS 로 나간다. 그래서 켜져 있다는 사실을 `/api/health` 로 화면까지 올린다.
"""

from __future__ import annotations

from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_VITE_CONFIG = _ROOT / "web" / "frontend" / "vite.config.ts"
_INDEX_HTML = _ROOT / "web" / "frontend" / "index.html"


@pytest.fixture(scope="module")
def vite_src() -> str:
    return _VITE_CONFIG.read_text(encoding="utf-8")


class TestCspProfiles:
    def test_index_html_has_no_hardcoded_csp(self):
        """CSP 가 index.html 에 박혀 있으면 프로파일이 의미가 없다."""
        html = _INDEX_HTML.read_text(encoding="utf-8")
        assert "%MM_CSP%" in html, "자리표시자가 사라졌다 — 빌드가 CSP 없이 나갈 수 있다"
        assert "connect-src" not in html.replace("connect-src 'self' — 외부", ""), \
            "index.html 에 CSP 가 다시 하드코딩됐다"

    def test_packaged_profile_is_self_only(self, vite_src):
        assert "packaged: \"connect-src 'self'\"" in vite_src, \
            "packaged 프로파일이 'self' 보다 넓어졌다"

    def test_standalone_profile_allows_openai_and_lan(self, vite_src):
        """단독 모드는 실제로 넓은 허용이 필요하다 — 좁히면 아이폰 앱이 무기능이 된다."""
        assert "standalone:" in vite_src
        assert "https://api.openai.com" in vite_src
        assert "ws: wss:" in vite_src

    def test_default_profile_is_the_narrow_one(self, vite_src):
        """프로파일 지정을 잊었을 때 **안전한 쪽**으로 실패해야 한다."""
        assert 'mode === "standalone" ? "standalone" : "packaged"' in vite_src

    def test_unsafe_eval_is_gone(self, vite_src):
        """G-04: 실제로 eval 을 요구하는 의존성이 0건임을 번들에서 확인했다
        (`new Function(`·`eval(` 모두 0건) → 허용을 남겨 둘 이유가 없다.

        주석에는 "넣지 않는다"는 설명이 남아 있으므로 **주석을 제외한 코드**만 본다.
        """
        code = "\n".join(l for l in vite_src.splitlines()
                         if not l.lstrip().startswith("//"))
        assert "unsafe-eval" not in code

    def test_build_fails_loudly_without_placeholder(self, vite_src):
        """CSP 가 조용히 빠지는 것이 최악이다 — 플러그인이 빌드를 실패시켜야 한다."""
        assert "throw new Error" in vite_src


class TestSslNotice:
    def _health(self, monkeypatch, verify):
        from meeting_minutes_app.common import config_loader
        from web.backend.app import health
        monkeypatch.setattr(config_loader, "get",
                            lambda k, d=None: verify if k == "ssl.verify" else d)
        monkeypatch.setattr(config_loader, "load_error", lambda: None)
        return health()

    def test_flagged_when_verification_disabled(self, monkeypatch):
        assert self._health(monkeypatch, False)["ssl_insecure"] is True

    def test_not_flagged_by_default(self, monkeypatch):
        assert self._health(monkeypatch, True)["ssl_insecure"] is False

    def test_default_is_verification_on(self):
        """스키마 기본값이 꺼짐이면 배너가 상시 뜨고 의미를 잃는다."""
        from meeting_minutes_app.common import config_schema
        field = next(f for f in config_schema.iter_fields()
                     if f["section"] == "ssl" and f["key"] == "verify")
        assert field["default"] is True

    def test_obsidian_local_rest_stays_exempt(self):
        """`obsidian.verify_ssl` 기본값 False 는 **정상**이다 — 127.0.0.1 의 자체서명
        로컬 REST 이므로 여기까지 켜면 Obsidian 연동이 깨진다. 외부 벤더 호출과 혼동해
        P0 로 올리면 안 된다(PRD 표기 정정 근거)."""
        from meeting_minutes_app.common import config_schema
        field = next(f for f in config_schema.iter_fields()
                     if f["section"] == "obsidian" and f["key"] == "verify_ssl")
        assert field["default"] is False
