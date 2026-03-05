#!/usr/bin/env python3
"""run_batch.py  —  음성/영상 파일 처리 런처

더블클릭하거나 파일을 bat 위에 드래그하거나:
    python run_batch.py [파일1] [파일2] ...
"""
from __future__ import annotations

import glob as _glob
import importlib.util
import logging
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

# ══════════════════════════════════════════════════════════════════
#  경로
# ══════════════════════════════════════════════════════════════════
BASE_DIR   = Path(__file__).parent.resolve()
OUTPUT_DIR = BASE_DIR / "output"
SCRIPT     = BASE_DIR / "meeting_minutes.py"
WATCHER    = BASE_DIR / "watcher.py"
LOG_FILE   = BASE_DIR / "run_py.log"

# 지원 확장자
SUPPORTED_EXT = {
    ".mp4", ".mp3", ".webm", ".wav", ".m4a", ".ogg",
    ".flac", ".aac", ".wma", ".mkv", ".avi", ".mov",
}

# ══════════════════════════════════════════════════════════════════
#  Windows: 콘솔 UTF-8 전환
# ══════════════════════════════════════════════════════════════════
if sys.platform == "win32":
    os.system("chcp 65001 > nul")
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# ══════════════════════════════════════════════════════════════════
#  로그
# ══════════════════════════════════════════════════════════════════
OUTPUT_DIR.mkdir(exist_ok=True)
logging.basicConfig(
    filename=str(LOG_FILE),
    level=logging.DEBUG,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    encoding="utf-8",
    filemode="a",
)
log = logging.getLogger("batch")
log.info("=== 배치 런처 시작  python=%s ===", sys.version.split()[0])

# ══════════════════════════════════════════════════════════════════
#  색상 지원
# ══════════════════════════════════════════════════════════════════
try:
    from colorama import init as _cinit, Fore, Style
    _cinit(autoreset=False)
    _C = {
        "title": Fore.CYAN  + Style.BRIGHT,
        "head":  Fore.WHITE + Style.BRIGHT,
        "ok":    Fore.GREEN + Style.BRIGHT,
        "warn":  Fore.YELLOW + Style.BRIGHT,
        "err":   Fore.RED   + Style.BRIGHT,
        "dim":   Style.DIM,
        "num":   Fore.YELLOW,
        "key":   Fore.CYAN,
        "rst":   Style.RESET_ALL,
    }
except ImportError:
    _C = {k: "" for k in ("title","head","ok","warn","err","dim","num","key","rst")}

def c(text, key: str) -> str:
    return _C[key] + str(text) + _C["rst"]

# ══════════════════════════════════════════════════════════════════
#  UI 헬퍼
# ══════════════════════════════════════════════════════════════════
W = 56

def cls():
    os.system("cls" if os.name == "nt" else "clear")

def ask(prompt: str = "  >> ") -> str:
    try:
        return input(prompt).strip()
    except (KeyboardInterrupt, EOFError):
        print()
        return ""

def banner(title: str, sub: str = "") -> str:
    eq = "═" * W
    out  = f"\n{c('  ' + eq, 'title')}\n"
    out += f"{c('  ' + title, 'title')}\n"
    if sub:
        out += c(f"  {sub}\n", "dim")
    out += c("  " + eq, "title")
    return out

def ruler(label: str = "") -> str:
    if label:
        pad = max(0, W - len(label) - 5)
        return c(f"  ── {label} {'─' * pad}", "dim")
    return c("  " + "─" * W, "dim")

def wait(msg: str = "  계속하려면 Enter..."):
    ask(msg)

# ══════════════════════════════════════════════════════════════════
#  처리 모드
# ══════════════════════════════════════════════════════════════════
MODES = {
    "1": {
        "label": "한국어 회의  →  한국어 회의록",
        "desc":  "한국어 직접 전사 · 번역 없음",
        "args":  ["--type", "meeting", "--language", "ko"],
    },
    "2": {
        "label": "영어 회의  →  한국어 회의록  (번역)",
        "desc":  "영어 전사 + 한국어 번역 · 한국어 회의록  ★ 추천",
        "args":  ["--type", "meeting", "--language", "en", "--translate"],
    },
    "3": {
        "label": "영어 회의  →  영어 회의록",
        "desc":  "번역 없음 · 영어 원문 그대로",
        "args":  ["--type", "meeting", "--language", "en"],
    },
    "4": {
        "label": "세미나  (영어 → 한국어 번역)",
        "desc":  "영어 전사 + 한국어 번역 · 한국어 세미나 기록",
        "args":  ["--type", "seminar", "--language", "en", "--translate"],
    },
    "5": {
        "label": "강의  (영어 → 한국어 번역)",
        "desc":  "영어 전사 + 한국어 번역 · 한국어 강의 노트",
        "args":  ["--type", "lecture", "--language", "en", "--translate"],
    },
    "6": {
        "label": "한국어 세미나  →  한국어 기록",
        "desc":  "한국어 직접 전사 · 번역 없음",
        "args":  ["--type", "seminar", "--language", "ko"],
    },
    "7": {
        "label": "한국어 강의  →  한국어 강의 노트",
        "desc":  "한국어 직접 전사 · 번역 없음",
        "args":  ["--type", "lecture", "--language", "ko"],
    },
}

# ══════════════════════════════════════════════════════════════════
#  패키지 의존성 체크
# ══════════════════════════════════════════════════════════════════
_PKG_IMPORT_MAP = {
    "python-dotenv": "dotenv",
    "Pillow":        "PIL",
    "PyYAML":        "yaml",
    "scikit-learn":  "sklearn",
    "opencv-python": "cv2",
}

def _check_deps() -> list:
    req = BASE_DIR / "requirements.txt"
    if not req.exists():
        return []
    missing = []
    for line in req.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        pkg = re.split(r"[>=<!;\s\[]", line)[0].strip()
        if not pkg:
            continue
        import_name = _PKG_IMPORT_MAP.get(pkg, pkg.replace("-", "_").lower())
        if importlib.util.find_spec(import_name) is None:
            missing.append(pkg)
    return missing

def screen_install_deps(missing: list) -> bool:
    cls()
    print(banner("필수 패키지 미설치", "실행 전에 설치가 필요합니다"))
    print()
    for pkg in missing:
        print(f"  {c('x', 'err')}  {pkg}")
    print()
    print(ruler())
    print()
    print(f"  {c('Y', 'num')}  지금 설치  (pip install -r requirements.txt)")
    print(f"  {c('0', 'num')}  종료")
    print()
    ch = ask("  Y/0 >> ").upper()

    if ch == "Y":
        cls()
        print("\n  패키지 설치 중...\n")
        log.info("pip install -r requirements.txt 실행")
        r = subprocess.run(
            [sys.executable, "-m", "pip", "install", "-r",
             str(BASE_DIR / "requirements.txt")]
        )
        if r.returncode == 0:
            print()
            print(c("  설치 완료!", "ok"))
            log.info("패키지 설치 성공")
            wait()
            return True
        else:
            print()
            print(c("  설치 실패. 수동으로 실행하세요:", "err"))
            print("  pip install -r requirements.txt")
            log.error("패키지 설치 실패")
            wait()
            return False

    sys.exit(0)

# ══════════════════════════════════════════════════════════════════
#  화면: config.json 없음
# ══════════════════════════════════════════════════════════════════
def screen_no_config():
    while True:
        cls()
        print(banner("config.json 없음", "최초 설정이 필요합니다"))
        print()
        print("  순서:")
        print(f"    1.  {c('copy config.example.json config.json', 'key')}")
        print()
        print('    2.  config.json 에서 "openai_api_key" 설정')
        print("        발급: https://platform.openai.com/api-keys")
        print()
        print(ruler())
        print()
        print(f"  {c('A', 'num')}   config.json 자동 생성 후 메모장 열기")
        print(f"  {c('0', 'num')}   종료")
        print()
        ch = ask().upper()

        if ch == "A":
            example = BASE_DIR / "config.example.json"
            cfg     = BASE_DIR / "config.json"
            if example.exists():
                shutil.copy(example, cfg)
                log.info("config.json 자동 생성")
            subprocess.Popen(["notepad", str(cfg)])
            wait("\n  config.json 저장 후 Enter...")
            if cfg.exists():
                return
        elif ch in ("0", ""):
            sys.exit(0)

# ══════════════════════════════════════════════════════════════════
#  파일 수집
# ══════════════════════════════════════════════════════════════════
def _collect_files(args: list) -> list:
    """경로(파일/폴더/glob) 목록에서 지원 미디어 파일만 수집."""
    result = []
    for arg in args:
        # glob 패턴 확장 시도
        expanded = _glob.glob(arg)
        if expanded:
            for p in expanded:
                p = Path(p).resolve()
                if p.is_file() and p.suffix.lower() in SUPPORTED_EXT:
                    result.append(str(p))
                elif p.is_dir():
                    result.extend(_collect_from_folder(p))
        else:
            p = Path(arg)
            if p.is_file():
                if p.suffix.lower() in SUPPORTED_EXT:
                    result.append(str(p.resolve()))
                else:
                    print(f"  {c('미지원 파일 건너뜀:', 'warn')} {p.name}")
            elif p.is_dir():
                result.extend(_collect_from_folder(p))
            else:
                print(f"  {c('경로 없음:', 'err')} {arg}")

    # 중복 제거, 정렬
    seen = set()
    out = []
    for f in result:
        if f not in seen:
            seen.add(f)
            out.append(f)
    return sorted(out)

def _collect_from_folder(folder: Path) -> list:
    """폴더에서 지원 확장자 파일 전부 수집 (정렬)."""
    files = []
    for ext in SUPPORTED_EXT:
        files.extend(folder.glob(f"*{ext}"))
        files.extend(folder.glob(f"*{ext.upper()}"))
    return sorted([str(f.resolve()) for f in set(files)])

# ══════════════════════════════════════════════════════════════════
#  미디어 파일 실행
# ══════════════════════════════════════════════════════════════════
def _run_batch(files: list, extra_args: list, title: str = ""):
    cmd = [sys.executable, str(SCRIPT)] + files + extra_args
    cmd += ["--output-dir", "output", "--notify", "email"]
    if title:
        cmd += ["--title", title]
    log.info("실행: %s", subprocess.list2cmdline(cmd))
    try:
        result = subprocess.run(cmd, cwd=str(BASE_DIR))
        log.info("종료 코드: %d", result.returncode)
        return result.returncode
    except FileNotFoundError:
        log.error("스크립트 없음: %s", SCRIPT)
        print(c(f"\n  오류: {SCRIPT.name} 파일을 찾을 수 없습니다\n", "err"))
        return -1

# ══════════════════════════════════════════════════════════════════
#  화면: 처리 모드 선택
# ══════════════════════════════════════════════════════════════════
def _select_mode() -> str | None:
    """MODES 선택 화면. 선택된 key 반환, 취소 시 None."""
    print()
    print(ruler("처리 모드 선택"))
    print()
    for key, mode in MODES.items():
        print(f"  {c(key, 'num')}  {mode['label']}")
        print(c(f"      {mode['desc']}", "dim"))
        print()
    print(f"  {c('0', 'key')}  취소")
    print()
    ch = ask("  1~7 / 0 >> ")
    if ch in MODES:
        return ch
    return None

# ══════════════════════════════════════════════════════════════════
#  화면: 파일 처리 실행
# ══════════════════════════════════════════════════════════════════
def screen_run_files(files: list):
    cls()
    print(banner("파일 처리"))
    print()

    # 파일 목록 표시
    shown = files[:10]
    for f in shown:
        try:
            rel = Path(f).relative_to(BASE_DIR)
        except ValueError:
            rel = Path(f).name
        print(f"  {c('+', 'ok')}  {rel}")
    if len(files) > 10:
        print(c(f"\n  ... 외 {len(files) - 10}개", "dim"))
    print()

    # 다중 파일: 출력 방식 선택
    title = ""
    if len(files) > 1:
        print(ruler("출력 방식"))
        print()
        print(f"  {c('1', 'num')}  파일별 개별 폴더 저장")
        print(f"  {c('2', 'num')}  하나의 폴더로 묶기  (제목 입력)")
        print()
        bch = ask("  1/2 >> ")
        if bch == "2":
            print()
            title = ask("  출력 폴더 제목 >> ")
            if not title:
                print(c("  제목 없음 → 개별 폴더로 처리합니다.", "warn"))
        print()

    # 모드 선택
    mode_key = _select_mode()
    if not mode_key:
        return

    mode = MODES[mode_key]
    cls()
    print()
    print(c("  " + "═" * W, "title"))
    print(c(f"  처리 시작  |  {mode['label']}", "title"))
    if title:
        print(c(f"  출력 제목: {title}", "dim"))
    print(c("  " + "═" * W, "title"))
    print()
    print(c("  처리 중입니다. 파일 크기에 따라 시간이 걸릴 수 있습니다.", "dim"))
    print()

    log.info("배치 처리 시작: mode=%s  files=%d  title=%s", mode_key, len(files), title or "-")
    ret = _run_batch(files, list(mode["args"]), title)

    if ret not in (0, None):
        print()
        print(c(f"  오류: 스크립트가 코드 {ret}로 종료되었습니다", "err"))
        print()
        print("  확인 사항:")
        print("    ‣ config.json 의 openai_api_key")
        print("    ‣ 네트워크 연결")
        print(f"    ‣ {LOG_FILE.name}  (프로젝트 루트)")
        print()
        wait()

    screen_done()

# ══════════════════════════════════════════════════════════════════
#  화면: 완료
# ══════════════════════════════════════════════════════════════════
def screen_done():
    # 최근 출력 폴더 탐색 (realtime_ 제외)
    candidates = sorted(
        [d for d in OUTPUT_DIR.iterdir()
         if d.is_dir() and not d.name.startswith("realtime_")],
        key=lambda d: d.stat().st_mtime,
        reverse=True,
    )
    latest = candidates[0] if candidates else None

    while True:
        print()
        print(ruler())
        print(c("  완료.", "ok"))
        if latest:
            try:
                rel = latest.relative_to(BASE_DIR)
            except ValueError:
                rel = latest
            print(f"\n  저장 위치: {c(rel, 'ok')}")

        print()
        print(f"  {c('O', 'key')}     = 저장 폴더 열기")
        print(f"  {c('Enter', 'key')} = 메인 메뉴로")
        print()
        ch = ask("  O/Enter >> ").upper()
        if ch == "O":
            open_folder(latest or OUTPUT_DIR)
        else:
            return

# ══════════════════════════════════════════════════════════════════
#  화면: 수동 파일 입력
# ══════════════════════════════════════════════════════════════════
def screen_input_manual():
    while True:
        cls()
        print(banner("파일 경로 입력"))
        print()
        print("  파일 경로를 입력하세요.")
        print(c("  (여러 파일: 공백으로 구분  /  *.mp4 형식 가능)", "dim"))
        print()
        print(c("  예: C:\\recordings\\meeting.mp4", "dim"))
        print(c("  예: C:\\recordings\\*.webm", "dim"))
        print()
        print(ruler())
        print()
        raw = ask()
        if not raw:
            return

        # 간단한 공백 분리 (경로에 공백이 없다고 가정; 따옴표 처리)
        parts = _split_paths(raw)
        files = _collect_files(parts)

        if not files:
            print()
            print(c("  처리할 파일이 없습니다.", "err"))
            wait()
            continue

        screen_run_files(files)
        return

def _split_paths(raw: str) -> list:
    """따옴표 처리를 포함한 경로 분리."""
    import shlex
    try:
        return shlex.split(raw, posix=False)
    except ValueError:
        return raw.split()

# ══════════════════════════════════════════════════════════════════
#  화면: 폴더 일괄 처리
# ══════════════════════════════════════════════════════════════════
def screen_input_folder():
    while True:
        cls()
        print(banner("폴더 일괄 처리"))
        print()
        print("  처리할 폴더 경로를 입력하세요.")
        print(c("  (폴더 내 모든 음성/영상 파일을 순서대로 처리)", "dim"))
        print()
        print(ruler())
        print()
        raw = ask()
        if not raw:
            return

        folder = Path(raw.strip('"').strip("'"))
        if not folder.exists():
            print(c(f"\n  폴더 없음: {folder}\n", "err"))
            wait()
            continue
        if not folder.is_dir():
            print(c(f"\n  폴더가 아닙니다: {folder}\n", "err"))
            wait()
            continue

        files = _collect_from_folder(folder)
        if not files:
            print()
            print(c(f"  [{folder.name}] 폴더에 처리할 파일이 없습니다.", "err"))
            print(c(f"  지원 형식: {', '.join(sorted(SUPPORTED_EXT))}", "dim"))
            print()
            wait()
            continue

        screen_run_files(files)
        return

# ══════════════════════════════════════════════════════════════════
#  화면: 감시 모드
# ══════════════════════════════════════════════════════════════════
def screen_watcher_mode():
    if not WATCHER.exists():
        print(c(f"\n  watcher.py 파일을 찾을 수 없습니다: {WATCHER}\n", "err"))
        wait()
        return

    while True:
        cls()
        print(banner("감시 모드", "폴더에 새 파일이 생기면 자동 처리합니다"))
        print()
        print("  처리 완료된 파일은 _processed/ 로 이동됩니다.")
        print("  종료: Ctrl+C")
        print()
        print(ruler())
        print()
        raw = ask("  감시할 폴더 경로 >> ")
        if not raw:
            return

        folder = Path(raw.strip('"').strip("'"))
        if not folder.exists():
            print(c(f"\n  폴더 없음: {folder}\n", "err"))
            wait()
            continue

        # 모드 선택
        mode_key = _select_mode()
        if not mode_key:
            return

        mode = MODES[mode_key]
        mode_args = list(mode["args"])

        # watcher.py 인자 조립
        cmd = [sys.executable, str(WATCHER), str(folder)]
        if "--type" in mode_args:
            cmd += ["--type", mode_args[mode_args.index("--type") + 1]]
        if "--translate" in mode_args:
            cmd.append("--translate")
        if "--language" in mode_args:
            cmd += ["--language", mode_args[mode_args.index("--language") + 1]]
        cmd += ["--notify", "email"]

        cls()
        print()
        print(c("  " + "═" * W, "title"))
        print(c(f"  감시 모드 시작  |  {mode['label']}", "title"))
        print(c(f"  폴더: {folder}", "dim"))
        print(c("  " + "═" * W, "title"))
        print()
        print(c("  Ctrl+C 로 종료합니다.", "dim"))
        print()

        log.info("감시 모드: %s  mode=%s", folder, mode_key)
        try:
            subprocess.run(cmd, cwd=str(BASE_DIR))
        except KeyboardInterrupt:
            pass

        print()
        print(c("  감시 모드 종료.", "ok"))
        wait()
        return

# ══════════════════════════════════════════════════════════════════
#  화면: 도움말
# ══════════════════════════════════════════════════════════════════
def screen_help():
    cls()
    print(banner("도움말"))
    print()

    _sections = [
        ("1. 파일 드래그앤드롭", [
            "run_batch.bat 위에 파일을 드래그하면 자동 감지됩니다.",
            "여러 파일을 한꺼번에 드래그할 수 있습니다.",
            "폴더를 드래그하면 폴더 내 모든 미디어 파일을 처리합니다.",
        ]),
        ("2. 지원 형식", [
            "음성: .mp3  .wav  .m4a  .ogg  .flac  .aac  .wma",
            "영상: .mp4  .webm  .mkv  .avi  .mov",
            "(영상은 오디오 트랙만 추출하여 처리)",
        ]),
        ("3. 다중 파일 처리", [
            "파일별 개별 폴더: 각 파일을 독립적으로 처리",
            "하나의 폴더로 묶기: --title 옵션으로 단일 출력 폴더에",
            "  01_파일명_, 02_파일명_ 접두사로 생성",
        ]),
        ("4. 감시 모드 (W)", [
            "지정 폴더에 새 파일이 감지되면 자동으로 처리합니다.",
            "처리 완료 파일 → _processed/ 서브폴더로 이동",
            "watchdog 패키지 필요: pip install watchdog",
        ]),
        ("5. 출력 파일", [
            "output/{날짜}_{제목}/",
            "  *_minutes.md    회의록",
            "  *_summary.md    요약",
            "  *_script.md     전체 텍스트",
        ]),
        ("6. API 키", [
            'config.json → "openai_api_key": "sk-proj-..."',
            "발급: https://platform.openai.com/api-keys",
        ]),
    ]

    for title, lines in _sections:
        print(c(f"  {title}", "head"))
        for line in lines:
            print(f"    {line}")
        print()

    print(ruler())
    print()
    print(f"  {c('C', 'key')}  config.json 메모장으로 열기")
    print(f"  {c('Enter', 'key')}  메인 메뉴로")
    print()
    ch = ask().upper()

    if ch == "C":
        cfg = BASE_DIR / "config.json"
        if not cfg.exists():
            shutil.copy(BASE_DIR / "config.example.json", cfg)
        subprocess.Popen(["notepad", str(cfg)])

# ══════════════════════════════════════════════════════════════════
#  화면: 메인 메뉴
# ══════════════════════════════════════════════════════════════════
def screen_main():
    while True:
        cls()
        print(banner(
            "음성/영상 파일 처리",
            "파일 → STT → 회의록 자동 저장",
        ))
        print()

        print(ruler("파일 입력"))
        print()
        print(f"  {c('F', 'num')}  파일 경로 입력")
        print(c("      (또는 bat 위에 파일을 드래그)", "dim"))
        print()
        print(f"  {c('D', 'num')}  폴더 선택  →  모든 미디어 파일 일괄 처리")
        print()
        print(f"  {c('W', 'num')}  감시 모드  →  폴더 모니터링 (자동 처리)")
        print()

        print(ruler("기타"))
        print()
        print(f"  {c('H', 'key')}  도움말")
        print(f"  {c('O', 'key')}  출력 폴더 열기")
        print(f"  {c('0', 'key')}  종료")
        print()
        print(ruler())
        print()
        ch = ask().upper()

        if ch == "F":
            screen_input_manual()
        elif ch == "D":
            screen_input_folder()
        elif ch == "W":
            screen_watcher_mode()
        elif ch == "H":
            screen_help()
        elif ch == "O":
            open_folder(OUTPUT_DIR)
        elif ch == "0":
            log.info("메인 메뉴에서 종료")
            sys.exit(0)

# ══════════════════════════════════════════════════════════════════
#  유틸
# ══════════════════════════════════════════════════════════════════
def open_folder(path: Path):
    path.mkdir(parents=True, exist_ok=True)
    log.info("폴더 열기: %s", path)
    if sys.platform == "win32":
        os.startfile(str(path))
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])

# ══════════════════════════════════════════════════════════════════
#  진입점
# ══════════════════════════════════════════════════════════════════
def main():
    log.info("python=%s  platform=%s  cwd=%s",
             sys.version.split()[0], sys.platform, Path.cwd())
    OUTPUT_DIR.mkdir(exist_ok=True)

    # 1. 패키지 의존성 체크
    missing = _check_deps()
    if missing:
        log.warning("미설치 패키지: %s", missing)
        screen_install_deps(missing)

    # 2. config.json 체크
    if not (BASE_DIR / "config.json").exists():
        screen_no_config()
        if not (BASE_DIR / "config.json").exists():
            return

    # 3. 드래그앤드롭 파일 처리
    dragged = _collect_files(sys.argv[1:])
    if dragged:
        log.info("드래그 파일 %d개 감지", len(dragged))
        screen_run_files(dragged)
    else:
        screen_main()


if __name__ == "__main__":
    main()
