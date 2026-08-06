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


class TestFrontendInstallIsReproducible:
    """프런트 설치도 lockfile 을 **그대로** 써야 한다.

    파이썬을 constraints 로 고정해도 프런트가 `npm install` 이면 재현성 구멍이 남는다 —
    `npm install` 은 package.json 의 범위(`^`)를 다시 해석해 lockfile 을 갱신할 수 있고,
    그러면 같은 커밋에서 빌드해도 다른 번들이 나온다. `npm ci` 는 lockfile 을 그대로
    설치하고 package.json 과 어긋나면 실패한다(그 실패가 조용한 드리프트보다 낫다).
    """

    def test_build_uses_npm_ci_not_install(self, ps1):
        assert "@('ci')" in ps1, "릴리즈 빌드가 npm ci 를 쓰지 않는다"
        assert "@('install')" not in ps1, (
            "npm install 이 남아 있다 — lockfile 이 갱신되면 같은 커밋에서 다른 번들이 나온다")

    def test_missing_lockfile_fails_loudly(self, ps1):
        """lockfile 이 없을 때 조용히 `npm install` 로 떨어지면 고정의 의미가 없다."""
        assert "package-lock.json 이 없습니다" in ps1

    def test_build_info_records_lock_hash(self, ps1):
        """파이썬만 각인하면 '같은 커밋인데 화면이 다르다' 의 원인을 좁힐 수 없다."""
        assert "package-lock sha256:" in ps1

    def test_lockfile_is_committed_and_in_sync(self):
        """`npm ci` 는 lockfile 과 package.json 이 어긋나면 빌드를 멈춘다 — 미리 잡는다."""
        import json
        root = Path(__file__).resolve().parent.parent / "web" / "frontend"
        pkg = json.loads((root / "package.json").read_text(encoding="utf-8"))
        lock = json.loads((root / "package-lock.json").read_text(encoding="utf-8"))
        lock_root = (lock.get("packages") or {}).get("") or {}
        for kind in ("dependencies", "devDependencies"):
            assert (pkg.get(kind) or {}) == (lock_root.get(kind) or {}), (
                f"{kind} 가 lockfile 과 어긋난다 — `npm install` 후 lockfile 을 커밋하세요")

    def test_test_script_exists_and_typechecks(self):
        """프런트 테스트가 실행 가능한 명령으로 존재하는지(UX-015 수용 기준)."""
        import json
        pkg = json.loads((Path(__file__).resolve().parent.parent / "web" / "frontend"
                          / "package.json").read_text(encoding="utf-8"))
        scripts = pkg.get("scripts") or {}
        assert "test" in scripts, "npm test 가 없으면 UX-015 는 미해소다"
        # 릴리즈 빌드가 테스트 타입검사에 인질이 되지 않도록 경로를 나눴다 — 그래도
        # 테스트 타입은 검사돼야 한다.
        assert "test:types" in scripts
        assert "test:types" in scripts["test"]


def _commands_only(text: str, comment_prefixes=("::", "rem ", "#")) -> str:
    """주석을 걷어낸 **실행 줄**만 남긴다.

    주석에 규칙을 설명해 두면 문자열 검사가 그 설명에 걸린다 — 실제로 두 번 걸렸다
    (`unsafe-eval`, `build:standalone`). 검사 대상은 코드여야 한다.
    """
    out = []
    for line in text.splitlines():
        t = line.strip().lower()
        if any(t.startswith(pfx) for pfx in comment_prefixes):
            continue
        out.append(line)
    return "\n".join(out)


@pytest.fixture(scope="module")
def exe_bat():
    return _commands_only((_BUILD / "build_exe.bat").read_text(encoding="utf-8", errors="replace"))


@pytest.fixture(scope="module")
def npm_scripts():
    import json
    return (json.loads((Path(__file__).resolve().parent.parent / "web" / "frontend"
                        / "package.json").read_text(encoding="utf-8"))).get("scripts") or {}


class TestAllBuildPathsAreConsistent:
    """빌드 경로가 셋이다 — 포터블(ps1) · exe(bat) · iOS(npm script).
    한쪽만 고치면 갈라진다(이 리포의 반복 사고 유형). 세 경로를 함께 고정한다.
    """

    def test_exe_build_also_uses_npm_ci(self, exe_bat):
        """포터블만 고치면 exe 경로에서 lockfile 이 갱신돼 번들이 갈라진다."""
        assert "npm ci" in exe_bat
        assert "call npm install" not in exe_bat

    def test_exe_build_fails_without_lockfile(self):
        raw = (_BUILD / "build_exe.bat").read_text(encoding="utf-8", errors="replace")
        assert "package-lock.json not found" in raw

    def test_ios_uses_the_standalone_csp_profile(self, npm_scripts):
        """아이폰 번들을 packaged 로 만들면 단독 모드가 CSP 에 막혀 통째로 죽는다.
        반대로 PC 배포본을 standalone 으로 만들면 좁혀 둔 보안이 풀린다."""
        for key in ("ios:sync", "ios:build"):
            assert "build:standalone" in npm_scripts.get(key, ""), f"{key} 가 standalone 프로파일이 아니다"

    def test_ios_release_build_installs_from_lockfile(self, npm_scripts):
        """배포용 산출물 경로는 재현돼야 한다. (반복 동기화용 ios:sync 는 제외 — 매번
        node_modules 를 지우면 느리고, 개발자가 직접 install 한다.)"""
        assert npm_scripts.get("ios:build", "").startswith("npm ci")

    def test_pc_builds_use_the_packaged_profile(self, exe_bat, ps1):
        """PC 경로(exe·포터블)는 백엔드가 프런트를 서빙하므로 `connect-src 'self'` 로 충분하다.
        **실행 줄만** 본다 — 주석에는 standalone 설명이 들어 있다."""
        assert "build:standalone" not in exe_bat
        assert "build:standalone" not in _commands_only(ps1, comment_prefixes=("#",))


class TestPortableZipGuards:
    """zip 단계의 두 관문 — 둘 다 **산출물을 실제로 열어 보고** 잡은 결함이다.
    빌드 로그의 SUCCESS 는 산출물이 맞다는 증거가 아니다(2026-08-06).
    """

    def test_build_verifies_the_bundle_csp_profile(self, ps1):
        """프런트 산출물의 CSP 를 확인하고 packaged 가 아니면 빌드를 실패시킨다.

        두 프로파일이 **같은 dist/ 를 쓰기 때문에**(vite 기본 outDir 하나) 아이폰용으로
        만든 번들이 남아 있으면 `connect-src` 가 임의 호스트까지 열린 채 배포된다."""
        cmds = _commands_only(ps1, comment_prefixes=("#",))
        assert "connect-src" in cmds, "산출물의 CSP 를 확인하지 않는다"
        assert "$cspSources" in cmds, "확인 결과로 분기하지 않는다"

    def test_zip_entry_names_are_normalized_to_forward_slashes(self, ps1):
        """ZIP 규격은 `/` 를 요구한다. .NET Framework 의 `CreateFromDirectory` 는 `\\` 를
        써서 7-Zip·macOS·unzip·python zipfile 이 폴더 구조 없이 파일 하나로 풀어버린다
        (실측 회귀 — 탐색기만 관대해서 통과처럼 보였다)."""
        cmds = _commands_only(ps1, comment_prefixes=("#",))
        assert "CreateEntryFromFile" in cmds
        assert r".Replace('\', '/')" in cmds, "엔트리 이름을 '/' 로 정규화하지 않는다"
        assert "CreateFromDirectory" not in cmds, "구분자가 `\\` 가 되는 API 로 되돌아갔다"
        assert "Compress-Archive" not in cmds, "깊은 트리에서 깨지는 경로로 되돌아갔다"

    def test_no_bytecode_ships_in_the_app_tree(self, ps1):
        """배포본 `app\\` 에는 소스만 담는다.

        5단계에서 `__pycache__` 를 지우지만 **7단계 스모크가 그 트리에서 import 를 돌려
        다시 만든다** — 순서 때문에 정리가 무력화돼 실제로 .pyc 30개가 배포됐다(실측).
        같은 커밋을 다시 빌드해도 zip 이 달라지고(무엇을 import 했는지가 산출물에 새겨진다)
        소스와 짝이 안 맞는 바이트코드가 섞인다. 그래서 셋으로 막는다: 스모크에
        `PYTHONDONTWRITEBYTECODE`, 스모크 뒤 재정리, 그리고 남아 있으면 빌드 실패."""
        cmds = _commands_only(ps1, comment_prefixes=("#",))
        assert "PYTHONDONTWRITEBYTECODE" in cmds, "스모크가 .pyc 를 쓰지 못하게 막지 않는다"
        assert cmds.count("__pycache__") >= 2, "스모크 뒤 재정리가 없다(5단계에만 있으면 무력)"
        assert ".pyc" in cmds, "남은 .pyc 를 검사해 실패시키지 않는다"

    def test_zip_excludes_user_data_but_restores_it(self, ps1):
        """사용자 데이터(config.json=API 키, 회의 DB)는 zip 에서 빠져야 하고, 압축이
        실패해도 **제자리로 돌아와야** 한다(삭제가 아니라 이동인 이유)."""
        cmds = _commands_only(ps1, comment_prefixes=("#",))
        assert "MeetingMinutesData" in cmds
        assert "$zipDataBak" in cmds and "finally" in cmds
