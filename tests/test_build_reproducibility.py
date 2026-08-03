"""배포 재현성 — 의존성 조합이 고정돼 있는지 (P1: 릴리즈 재현성).

`requirements-web.txt` 는 `>=` 범위라 pip 이 **빌드 시점에** 버전을 정한다. 그래서
같은 커밋을 다시 빌드해도 다른 조합이 나오고, 더 나쁜 것은 **테스트가 검증한 조합과
배포되는 조합이 다르다**는 점이다. 2026-08-03 실측:

    패키지      개발환경(테스트가 도는 곳)   배포본(사용자가 받는 것)
    uvicorn     0.41.0                      0.52.1
    fastapi     0.135.1                     0.141.1
    openai      2.46.0                      2.52.0
    pydantic    2.12.5                      2.13.4

프런트엔드는 `package-lock.json` 으로 이미 고정돼 있었고 파이썬 쪽만 열려 있었다.
여기서 고정하는 것은 **드리프트**다 — 새 의존성을 추가했는데 constraints 갱신을 잊는 것.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_BUILD = Path(__file__).resolve().parent.parent / "scripts" / "build"
_REQ = _BUILD / "requirements-web.txt"
_CON = _BUILD / "constraints-web.txt"
_PS1 = _BUILD / "build_portable.ps1"


def _requirement_names(text: str) -> set[str]:
    """requirements 파일에서 배포 대상 패키지 이름만 뽑는다(주석·빈 줄·extras 제외)."""
    names = set()
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        name = re.split(r"[<>=!~\[]", line, maxsplit=1)[0].strip()
        if name:
            names.add(name.lower().replace("_", "-"))
    return names


def _pinned(text: str) -> dict[str, str]:
    pins = {}
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if "==" in line:
            n, v = line.split("==", 1)
            pins[n.strip().lower().replace("_", "-")] = v.strip()
    return pins


@pytest.fixture(scope="module")
def req():
    return _requirement_names(_REQ.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def pins():
    return _pinned(_CON.read_text(encoding="utf-8"))


class TestConstraintsCoverDirectDeps:
    def test_constraints_file_exists(self):
        assert _CON.exists(), (
            "constraints-web.txt 가 없으면 배포본 버전이 빌드 시점에 결정된다 — "
            "같은 커밋에서 같은 zip 이 나오지 않는다")

    def test_every_direct_requirement_is_pinned(self, req, pins):
        """새 의존성을 추가하고 constraints 갱신을 잊는 것이 이 테스트가 막는 드리프트다."""
        missing = sorted(n for n in req if n not in pins)
        assert not missing, (
            f"constraints-web.txt 에 고정되지 않은 직접 의존성: {missing}. "
            f"실기 검증 후 새 조합으로 갱신하세요(파일 머리말 참고).")

    def test_pins_are_exact(self, pins):
        """`>=` 로 적으면 고정이 아니다."""
        assert pins, "고정 항목이 하나도 없다"
        for name, ver in pins.items():
            assert re.fullmatch(r"[0-9][0-9A-Za-z.\-+!]*", ver), f"{name} 의 버전이 정확하지 않다: {ver}"

    def test_transitive_deps_are_pinned_too(self, req, pins):
        """전이 의존성이 열려 있으면 고정의 의미가 없다 — numpy·onnxruntime 처럼 큰
        네이티브 패키지가 조용히 올라가는 것이 실제 위험이다."""
        transitive = set(pins) - req
        assert len(transitive) >= 20, (
            f"전이 의존성 고정이 {len(transitive)}개뿐이다 — pip freeze 로 만든 조합이 아닌 것 같다")
        for critical in ("numpy", "onnxruntime", "starlette", "pydantic-core", "tokenizers"):
            assert critical in pins, f"{critical} 가 고정되지 않았다"


@pytest.fixture(scope="module")
def ps1():
    return _PS1.read_text(encoding="utf-8")


class TestBuildScriptUsesConstraints:

    def test_pip_install_passes_constraints(self, ps1):
        assert "'-c',$constraints" in ps1, "빌드가 constraints 를 pip 에 넘기지 않는다"

    def test_missing_constraints_warns_but_does_not_block(self, ps1):
        """갱신 절차가 '지우고 빌드' 이므로 부재는 실패가 아니라 경고여야 한다."""
        assert "[경고] constraints-web.txt" in ps1

    def test_build_info_records_the_combination(self, ps1):
        """어느 조합으로 빌드된 zip 인지 배포본 안에서 확인할 수 있어야 한다."""
        assert "constraints sha256:" in ps1


class TestFrontendIsAlreadyLocked:
    def test_package_lock_exists(self):
        """파이썬만 열려 있었다는 근거 — 프런트는 이미 고정돼 있었다."""
        lock = Path(__file__).resolve().parent.parent / "web" / "frontend" / "package-lock.json"
        assert lock.exists() and lock.stat().st_size > 1000


class TestDeclarationsMatchReality:
    def test_pyproject_numpy_range_allows_v2(self):
        """`numpy~=1.24`(=1.x 만) 로 선언돼 있었는데 개발환경도 배포본도 2.x 였다 —
        선언이 사실과 다르면 새 개발자가 또 다른 조합을 얻는다."""
        text = (Path(__file__).resolve().parent.parent / "pyproject.toml").read_text(encoding="utf-8")
        line = next(l for l in text.splitlines() if l.strip().startswith('"numpy'))
        assert "~=1.24" not in line, "numpy 선언이 1.x 만 허용한다 — 실제 배포는 2.x 다"
