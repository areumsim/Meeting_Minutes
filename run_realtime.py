#!/usr/bin/env python3
"""run_realtime.py  —  실시간 회의 녹취 런처

더블클릭하거나:
    python run_realtime.py
"""
from __future__ import annotations

import importlib.util
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

# ══════════════════════════════════════════════════════════════════
#  경로
# ══════════════════════════════════════════════════════════════════
BASE_DIR       = Path(__file__).parent.resolve()
OUTPUT_DIR     = BASE_DIR / "output"
ACTIVE_SESSION = OUTPUT_DIR / ".active_session"
SCRIPT         = BASE_DIR / "realtime_transcription.py"
LOG_FILE       = BASE_DIR / "run_py.log"

# ══════════════════════════════════════════════════════════════════
#  Windows: 콘솔 UTF-8 전환 (한글 출력)
# ══════════════════════════════════════════════════════════════════
if sys.platform == "win32":
    os.system("chcp 65001 > nul")
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# ══════════════════════════════════════════════════════════════════
#  로그 (창 닫혀도 남음)
# ══════════════════════════════════════════════════════════════════
OUTPUT_DIR.mkdir(exist_ok=True)
logging.basicConfig(
    filename=str(LOG_FILE),
    level=logging.DEBUG,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    encoding="utf-8",
    filemode="a",
)
log = logging.getLogger("launcher")
log.info("=== 시작  python=%s ===", sys.version.split()[0])

# ══════════════════════════════════════════════════════════════════
#  색상 지원  (pip install colorama)
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
#  녹취 모드
# ══════════════════════════════════════════════════════════════════
MODES = {
    "1": {
        "label": "한국어 회의  →  한국어 회의록",
        "desc":  "한국어 직접 전사 · 번역 없음",
        "cost":  "~$0.24/hr",
        "args":  ["--language", "ko",
                  "--model", "gpt-4o-mini-transcribe",
                  "--type", "meeting"],
    },
    "2": {
        "label": "영어 회의  →  한국어 회의록  (실시간 번역)",
        "desc":  "녹취 중 한국어 번역 실시간 표시 + 한국어 회의록",
        "cost":  "~$0.25/hr  ★ 추천",
        "args":  ["--language", "en", "--translate",
                  "--translate-model", "gpt-4o-mini",
                  "--model", "gpt-4o-mini-transcribe",
                  "--type", "meeting"],
    },
    "3": {
        "label": "영어 회의  →  영어 회의록",
        "desc":  "번역 표시 없음 · 영어 원문 그대로 전사",
        "cost":  "~$0.24/hr",
        "args":  ["--language", "en",
                  "--model", "gpt-4o-mini-transcribe",
                  "--type", "meeting"],
    },
    "4": {
        "label": "세미나 / 발표  (영어 → 한국어, 실시간 번역)",
        "desc":  "녹취 중 한국어 번역 표시 + 한국어 세미나 기록",
        "cost":  "~$0.25/hr",
        "args":  ["--language", "en", "--translate",
                  "--translate-model", "gpt-4o-mini",
                  "--model", "gpt-4o-mini-transcribe",
                  "--type", "seminar"],
    },
    "5": {
        "label": "강의  (영어 → 한국어, 실시간 번역)",
        "desc":  "녹취 중 한국어 번역 표시 + 한국어 강의 노트",
        "cost":  "~$0.25/hr",
        "args":  ["--language", "en", "--translate",
                  "--translate-model", "gpt-4o-mini",
                  "--model", "gpt-4o-mini-transcribe",
                  "--type", "lecture"],
    },
    "6": {
        "label": "한국어 세미나 / 발표  →  한국어 기록",
        "desc":  "한국어 직접 전사 · 번역 없음",
        "cost":  "~$0.24/hr",
        "args":  ["--language", "ko",
                  "--model", "gpt-4o-mini-transcribe",
                  "--type", "seminar"],
    },
    "7": {
        "label": "한국어 강의  →  한국어 강의 노트",
        "desc":  "한국어 직접 전사 · 번역 없음",
        "cost":  "~$0.24/hr",
        "args":  ["--language", "ko",
                  "--model", "gpt-4o-mini-transcribe",
                  "--type", "lecture"],
    },
}

# ══════════════════════════════════════════════════════════════════
#  API 비용 추정
# ══════════════════════════════════════════════════════════════════
_STT_PRICE_PER_MIN = {   # $/min
    "gpt-4o-mini-transcribe":            0.003,
    "gpt-4o-mini-transcribe-2025-12-15": 0.003,
    "gpt-4o-transcribe":                 0.006,
    "gpt-4o-transcribe-diarize":         0.006,
    "whisper-1":                         0.006,
}
_MINUTES_COST_PER_SESSION = 0.08   # gpt-4o 회의록 생성 1회 (~20K in + 3K out)
_TRANSLATE_COST_PER_MIN   = 0.0002  # gpt-4o-mini 번역 (~173 tokens/min × 2 방향)

def _compute_cost(mode_key: str, elapsed_sec: float) -> dict:
    mode = MODES[mode_key]
    args = mode["args"]
    stt_model = args[args.index("--model") + 1] if "--model" in args else "gpt-4o-mini-transcribe"
    translate = "--translate" in args
    elapsed_min = elapsed_sec / 60
    stt      = _STT_PRICE_PER_MIN.get(stt_model, 0.003) * elapsed_min
    trans    = _TRANSLATE_COST_PER_MIN * elapsed_min if translate else 0.0
    minutes  = _MINUTES_COST_PER_SESSION
    return {
        "stt":          round(stt, 5),
        "translate":    round(trans, 5),
        "minutes":      round(minutes, 4),
        "total":        round(stt + trans + minutes, 4),
        "elapsed_min":  elapsed_min,
        "stt_model":    stt_model,
        "has_translate": translate,
    }

# ══════════════════════════════════════════════════════════════════
#  세션 머지 상태
# ══════════════════════════════════════════════════════════════════
_merge_prev: Optional[str] = None

def _set_merge(path: Optional[str]):
    global _merge_prev
    _merge_prev = path

# ══════════════════════════════════════════════════════════════════
#  패키지 의존성 체크
# ══════════════════════════════════════════════════════════════════

# requirements.txt 패키지명 → import명이 다른 경우
_PKG_IMPORT_MAP = {
    "python-dotenv": "dotenv",
    "Pillow":        "PIL",
    "PyYAML":        "yaml",
    "scikit-learn":  "sklearn",
    "opencv-python": "cv2",
}

def _check_deps() -> list:
    """requirements.txt 기준으로 미설치 패키지 목록 반환."""
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
    """미설치 패키지를 보여주고 설치 여부를 묻는다. 설치 성공 시 True."""
    cls()
    print(banner("⚠  필수 패키지 미설치", "실행 전에 설치가 필요합니다"))
    print()
    for pkg in missing:
        print(f"  {c('✗', 'err')}  {pkg}")
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
            print(c("  ✓ 설치 완료!", "ok"))
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
#  메인 스크립트 실행
# ══════════════════════════════════════════════════════════════════
def _run_script(extra_args: list) -> int:
    cmd = [sys.executable, str(SCRIPT)] + extra_args
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
#  화면: 이전 세션 미완료 (크래시 복구)
# ══════════════════════════════════════════════════════════════════
def _get_active_session() -> tuple:
    if not ACTIVE_SESSION.exists():
        return None, ""
    lines = ACTIVE_SESSION.read_text(encoding="utf-8").splitlines()
    path  = lines[0].strip() if lines         else ""
    time_ = lines[1].strip() if len(lines) > 1 else ""
    if path and Path(path).exists():
        return path, time_
    return None, ""

def screen_session_interrupted(prev_log: str, prev_time: str):
    while True:
        cls()
        print(banner(
            "⚠  이전 세션이 완료되지 않았습니다",
            "프로그램이 예기치 않게 종료되었을 수 있습니다.",
        ))
        print(f"  파일 : {c(prev_log, 'warn')}")
        print(f"  시각 : {prev_time}")
        print()
        print(ruler())
        print()
        print(f"  {c('1', 'num')}  이어서 녹취 + 합본 생성  {c('(추천)', 'dim')}")
        print(      "       기존 transcript + 새 녹취를 합쳐 회의록 생성")
        print()
        print(f"  {c('2', 'num')}  이전 세션 회의록만 생성")
        print(      "       재녹취 없이 저장된 transcript로 회의록 생성")
        print()
        print(f"  {c('3', 'num')}  이전 세션 무시 후 새로 시작")
        print(      "       복구 마커만 제거 · 세션 파일은 R 메뉴에서 복구 가능")
        print()
        ch = ask("  1/2/3 >> ")

        if ch == "1":
            _set_merge(prev_log)
            log.info("이어서+합본  prev=%s", prev_log)
            return

        elif ch == "2":
            cls()
            print("\n  저장된 세션에서 회의록 생성 중...\n")
            log.info("이전 세션 복구만  prev=%s", prev_log)
            _run_script(["--recover", prev_log, "--email", "--output-dir", "output"])
            ACTIVE_SESSION.unlink(missing_ok=True)
            screen_done()
            return

        elif ch == "3":
            print()
            print(c("  이전 세션을 무시하고 새로 시작하시겠습니까?", "warn"))
            print(f"  {c('Y', 'num')} = 확인   {c('Enter', 'key')} = 취소")
            confirm = ask("  Y/Enter >> ").upper()
            if confirm == "Y":
                ACTIVE_SESSION.unlink(missing_ok=True)
                log.info("이전 세션 무시 후 새로 시작")
                return

        elif ch == "0":
            sys.exit(0)

# ══════════════════════════════════════════════════════════════════
#  화면: PCM 오디오 백업 복구
# ══════════════════════════════════════════════════════════════════
def screen_pcm_recovery():
    pcm_files = sorted(OUTPUT_DIR.glob("**/*_audio.pcm"))
    if not pcm_files:
        return

    while True:
        cls()
        print(banner(
            "⚠  변환되지 않은 오디오 백업 발견",
            "WAV 변환이 완료되지 않았습니다 (크래시?)",
        ))
        print(f"  {c(len(pcm_files), 'warn')}개 파일:\n")
        for f in pcm_files:
            try:
                rel = f.relative_to(BASE_DIR)
            except ValueError:
                rel = f
            print(f"    {rel}")
        print()
        print(ruler())
        print()
        print(f"  {c('1', 'num')}  ffmpeg으로 자동 변환  (변환 성공 시 PCM 삭제)")
        print()
        print(f"  {c('2', 'num')}  출력 폴더 열기  (수동 처리)")
        print()
        print(f"  {c('3', 'num')}  나중에 처리")
        print()
        ch = ask("  1/2/3 >> ")

        if ch == "1":
            _do_pcm_convert(pcm_files)
            return
        elif ch == "2":
            open_folder(OUTPUT_DIR)
            pcm_files = sorted(OUTPUT_DIR.glob("**/*_audio.pcm"))
            if not pcm_files:
                return
        elif ch in ("3", ""):
            return
        elif ch == "0":
            sys.exit(0)

def _do_pcm_convert(pcm_files: list):
    cls()
    print("\n  PCM → WAV 변환 중...\n")
    any_fail = False
    for pcm in pcm_files:
        wav = pcm.with_suffix(".wav")
        print(f"  {pcm.name}  ...  ", end="", flush=True)
        r = subprocess.run(
            ["ffmpeg", "-y", "-f", "s16le", "-ar", "16000",
             "-ac", "1", "-i", str(pcm), str(wav)],
            capture_output=True,
        )
        if r.returncode == 0:
            pcm.unlink()
            print(c("완료", "ok"))
            log.info("PCM 변환 완료: %s", pcm.name)
        else:
            print(c("실패", "err"))
            any_fail = True
            log.error("PCM 변환 실패: %s\n%s",
                      pcm.name, r.stderr.decode(errors="replace")[:300])
    if any_fail:
        print()
        print(c("  ffmpeg를 찾을 수 없거나 변환 오류입니다.", "err"))
        print("  설치: https://www.gyan.dev/ffmpeg/builds/")
    print()
    wait()

# ══════════════════════════════════════════════════════════════════
#  화면: 메인 메뉴
# ══════════════════════════════════════════════════════════════════
def screen_main():
    while True:
        cls()
        print(banner(
            "실시간 회의 녹취",
            "마이크 → 실시간 STT → 회의록 자동 저장",
        ))
        print()
        if _merge_prev:
            print(c("  [*] 이전 세션과 합본으로 생성됩니다\n", "warn"))

        print(ruler("언어 / 모드"))
        print()
        for key, mode in MODES.items():
            print(f"  {c(key, 'num')}  {mode['label']}  {c(mode['cost'], 'dim')}")
            print(c(f"      {mode['desc']}", "dim"))
            print()

        print(ruler("기타"))
        print()
        print(f"  {c('H', 'key')}  도움말 / 설치 가이드")
        print(f"  {c('R', 'key')}  이전 세션 복구")
        print(f"  {c('O', 'key')}  출력 폴더 열기")
        print(f"  {c('0', 'key')}  종료")
        print()
        print(ruler())
        print()
        ch = ask().upper()

        if ch in MODES:
            screen_run_mode(ch)
        elif ch == "H":
            screen_help()
        elif ch == "R":
            screen_recover_menu()
        elif ch == "O":
            open_folder(OUTPUT_DIR)
        elif ch == "0":
            log.info("메인 메뉴에서 종료")
            sys.exit(0)

# ══════════════════════════════════════════════════════════════════
#  화면: 녹취 세션 실행
# ══════════════════════════════════════════════════════════════════
def _ask_recording_mode() -> tuple:
    """녹음 방식 선택. (use_vad: bool, ws_mode: str) 반환.
       ws_mode: "http" | "ws"
    """
    vad_avail = importlib.util.find_spec("webrtcvad") is not None
    ws_avail  = importlib.util.find_spec("websockets") is not None
    while True:
        cls()
        print()
        print(ruler("녹음 방식 선택"))
        print()
        print(f"  {c('1', 'num')}  Standard   —  3초 고정 청크 {c('(안정적)', 'dim')}")
        print(f"      지연: 영어 3~4초  |  한국어 4~5초")
        print()
        if vad_avail:
            print(f"  {c('2', 'num')}  VAD        —  침묵 감지 즉시 전송 {c('(빠름)', 'ok')}")
            print(f"      지연: 짧은 응답 2~3초  |  긴 문장 4~5초")
            print(f"      침묵 구간 API 호출 없음 (비용 절약)")
        else:
            print(f"  {c('2', 'num')}  VAD        —  {c('webrtcvad 미설치', 'err')}")
            print(f"      {c('pip install webrtcvad-wheels', 'dim')}")
        print()
        if ws_avail:
            print(f"  {c('3', 'num')}  WebSocket  —  실시간 스트리밍 {c('(가장 빠름)', 'ok')}")
            print(f"      지연: ~1초  |  서버 VAD + 노이즈 리덕션 내장")
            print(f"      비용: STT ~$0.01/min (Standard의 ~3배)")
        else:
            print(f"  {c('3', 'num')}  WebSocket  —  {c('websockets 미설치', 'err')}")
            print(f"      {c('pip install websockets', 'dim')}")
        print()
        print(ruler())
        print()
        ch = ask("  1/2/3 >> ").strip()
        if ch == "1":
            return (False, "http")
        if ch == "2":
            if vad_avail:
                return (True, "http")
            print()
            print(c("  webrtcvad 미설치 → Standard 모드로 시작합니다.", "warn"))
            time.sleep(2)
            return (False, "http")
        if ch == "3":
            if ws_avail:
                return (False, "ws")
            print()
            print(c("  websockets 미설치 → Standard 모드로 시작합니다.", "warn"))
            time.sleep(2)
            return (False, "http")
        if ch == "0":
            return (False, "http")


def _ask_topic() -> str:
    """회의/세미나 주제 입력 (선택 사항). 빈 문자열 반환 시 건너뜀."""
    cls()
    print()
    print(ruler("회의 주제 입력  (선택사항)"))
    print()
    print(f"  주제를 입력하면 번역·회의록·요약 품질이 향상됩니다.")
    print(c("  Enter만 누르면 건너뜁니다.", "dim"))
    print()
    topic = ask("  주제 >> ")
    return topic


def _ask_memo() -> Optional[Path]:
    """메모/노트 파일 경로 입력 또는 직접 붙여넣기.

    반환: 메모가 저장된 Path, 또는 None(건너뜀).
    붙여넣기 모드일 때는 임시 파일을 생성하고 경로를 반환함.
    """
    cls()
    print()
    print(ruler("메모 / 노트 추가"))
    print()
    print(f"  {c('1', 'num')}  파일 경로 입력  (txt, md)")
    print(f"  {c('2', 'num')}  직접 붙여넣기")
    print(f"  {c('Enter', 'key')} 건너뜀")
    print()
    ch = ask("  1/2/Enter >> ")

    if ch == "1":
        print()
        path_str = ask("  파일 경로 >> ").strip().strip('"').strip("'")
        if not path_str:
            return None
        p = Path(path_str)
        if not p.exists():
            print()
            print(c(f"  파일을 찾을 수 없습니다: {p}", "err"))
            wait()
            return None
        return p

    elif ch == "2":
        print()
        print(c("  내용을 붙여넣은 후:", "dim"))
        print(c("    Windows : 빈 줄에서 Ctrl+Z  Enter", "dim"))
        print(c("    Linux/Mac: 빈 줄에서 Ctrl+D", "dim"))
        print()
        lines: list[str] = []
        try:
            while True:
                lines.append(input())
        except EOFError:
            pass
        if not lines:
            return None
        memo_text = "\n".join(lines).strip()
        if not memo_text:
            return None
        tmp = OUTPUT_DIR / f"_memo_paste_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        tmp.write_text(memo_text, encoding="utf-8")
        return tmp

    return None


def _supplement_with_memo(folder: Path, memo_path: Path) -> bool:
    """세션 폴더의 JSONL 로그로 메모를 반영한 회의록/요약을 재생성.

    반환: 성공 여부.
    """
    # 세션 JSONL 탐색
    jsonl_files = sorted(folder.glob("session_*.jsonl"), reverse=True)
    if not jsonl_files:
        print()
        print(c("  세션 로그(session_*.jsonl)를 찾을 수 없습니다.", "err"))
        wait()
        return False
    jsonl = jsonl_files[0]

    # 메타에서 topic 복원 (있으면)
    topic = ""
    meta_files = list(folder.glob("*_meta.json"))
    if meta_files:
        try:
            with open(meta_files[0], encoding="utf-8") as f:
                meta = json.load(f)
            topic = meta.get("topic", "")
        except Exception:
            pass

    args = [
        "--recover", str(jsonl),
        "--output-dir", str(folder),
        "--memo", str(memo_path),
    ]
    if topic:
        args += ["--topic", topic]

    print()
    print(ruler("메모 반영 재생성 중..."))
    ret = _run_script(args)

    # 붙여넣기로 만든 임시 파일은 성공 후 정리
    if memo_path.name.startswith("_memo_paste_"):
        try:
            memo_path.unlink()
        except OSError:
            pass

    return ret in (0, None)


def screen_run_mode(key: str):
    mode    = MODES[key]
    translate = "--translate" in mode["args"]
    use_vad, ws_mode = _ask_recording_mode()
    topic = _ask_topic()

    cls()

    # ── 컴팩트 헤더 (스크롤해서 참고할 수 있도록 최소 높이 유지) ──
    if ws_mode == "ws":
        mode_label = c(" [WebSocket]", "ok")
        delay_hint = "~1초"
    elif use_vad:
        mode_label = c(" [VAD]", "ok")
        delay_hint = "2~3초 (짧은 응답)"
    else:
        mode_label = ""
        delay_hint = "3~5초"
    print()
    print(c("  " + "═" * W, "title"))
    print(c(f"  ● 녹취 중  |  {mode['label']}", "title") + mode_label)
    if topic:
        print(c(f"  주제: {topic}", "warn"))
    print(c("  " + "─" * W, "dim"))
    # 출력 예시 (번역 여부에 따라)
    if translate:
        print(c("  [00:05]", "dim") + " Let's start with the quarterly results")
        print(c("           → 분기 실적부터 시작하겠습니다", "warn"))
    elif "ko" in mode["args"]:
        print(c("  [00:05]", "dim") + " 분기 실적 회의를 시작하겠습니다")
    else:
        print(c("  [00:05]", "dim") + " Let's start with the quarterly results")
    print(c("  " + "─" * W, "dim"))
    print(f"  {c('q+Enter', 'key')} 종료   "
          f"{c('p+Enter', 'key')} 일시정지   "
          f"{c('r+Enter', 'key')} 재개   "
          f"{c('Ctrl+C', 'key')} 강제종료   "
          f"{c(delay_hint, 'dim')}")
    print(c("  " + "═" * W, "title"))
    print()

    merge_snapshot = _merge_prev
    _set_merge(None)

    args = list(mode["args"]) + ["--email", "--output-dir", "output"]
    if ws_mode == "ws":
        args += ["--mode", "ws"]
    elif use_vad:
        args.append("--vad")
    if merge_snapshot:
        args += ["--prev-session", merge_snapshot]
    if topic:
        args += ["--topic", topic]

    log.info("세션 시작: mode=%s  merge=%s  topic=%s", key, merge_snapshot, topic or "(없음)")
    t_start = time.time()
    ret = _run_script(args)
    elapsed = time.time() - t_start

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

    screen_done(mode_key=key, elapsed_sec=elapsed)

# ══════════════════════════════════════════════════════════════════
#  화면: 세션 완료
# ══════════════════════════════════════════════════════════════════
def screen_done(mode_key: str = "", elapsed_sec: float = 0.0):
    folders = sorted(OUTPUT_DIR.glob("realtime_*"), reverse=True)
    latest  = folders[0] if folders else None

    # 비용 추정 (세션이 10초 이상 실행된 경우만)
    cost = None
    if mode_key and elapsed_sec > 10:
        cost = _compute_cost(mode_key, elapsed_sec)
        em = int(cost["elapsed_min"])
        es = int((cost["elapsed_min"] - em) * 60)
        log.info(
            "세션 종료: 모드=%s  시간=%d분%d초  STT=$%.5f  번역=$%.5f  회의록=$%.4f  합계=$%.4f",
            mode_key, em, es,
            cost["stt"], cost["translate"], cost["minutes"], cost["total"],
        )

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

        if cost:
            em = int(cost["elapsed_min"])
            es = int((cost["elapsed_min"] - em) * 60)
            print()
            print(ruler("이번 세션 비용 추정"))
            print()
            print(f"  녹취 시간  :  {em}분 {es}초")
            print(f"  STT        :  ${cost['stt']:.4f}  ({cost['stt_model']})")
            if cost["has_translate"]:
                print(f"  번역       :  ${cost['translate']:.4f}  (gpt-4o-mini)")
            print(f"  회의록     :  ${cost['minutes']:.4f}  (gpt-4o, 고정)")
            total_str = f"${cost['total']:.4f}"
            print(f"  {c('합계', 'head')}       :  {c(total_str, 'warn')}")

        print()
        print(f"  {c('M', 'key')}     = 메모/노트 추가 후 회의록 재생성")
        print(f"  {c('O', 'key')}     = 저장 폴더 열기")
        print(f"  {c('Enter', 'key')} = 메인 메뉴로")
        print()
        ch = ask("  M/O/Enter >> ").upper()
        if ch == "M":
            if not latest:
                print(c("  최근 세션 폴더를 찾을 수 없습니다.", "err"))
                wait()
                continue
            memo_path = _ask_memo()
            if memo_path:
                _supplement_with_memo(latest, memo_path)
                # 재생성 후 latest 폴더 갱신 (혹시 새로 만들어진 경우 대비)
                folders = sorted(OUTPUT_DIR.glob("realtime_*"), reverse=True)
                latest  = folders[0] if folders else latest
        elif ch == "O":
            open_folder(latest or OUTPUT_DIR)
        else:
            return

# ══════════════════════════════════════════════════════════════════
#  화면: 도움말
# ══════════════════════════════════════════════════════════════════
def screen_help():
    cls()
    print(banner("도움말 / 설치 가이드"))
    print()

    _sections = [
        ("1. API 키  (필수)", [
            'config.json →  "openai_api_key": "sk-proj-..."',
            "발급: https://platform.openai.com/api-keys",
        ]),
        ("2. 이메일 알림  (선택)", [
            'config.json → "email" 섹션:',
            '  sender   : 발신자@example.com',
            '  password : 앱 비밀번호  (로그인 비밀번호 아님)',
            '  recipient: 수신자@example.com',
            "",
            "Gmail: https://myaccount.google.com/apppasswords",
            "Naver: 메일 설정 → POP3/SMTP 사용 → 비밀번호 발급",
        ]),
        ("3. 패키지 설치  (최초 1회)", [
            "pip install -r requirements.txt",
        ]),
        ("4. 출력 파일", [
            "output/realtime_YYYYMMDD_HHMMSS/",
            "  *_minutes.md       회의록",
            "  *_summary.md       요약",
            "  *_transcript.txt   전체 텍스트",
            "  session_*.jsonl    세션 로그 (복구용)",
            "  session_*_audio.wav  오디오 백업",
        ]),
        ("5. 녹취 중지 / 일시정지", [
            "q+Enter  = 정상 종료 → 회의록 자동 생성",
            "p+Enter  = 일시정지  |  r+Enter = 재개",
            "Ctrl+C   = 강제 종료 → 다음 실행 시 복구 메뉴 표시",
        ]),
        ("6. 비용 안내 (시간당)", [
            "한국어 회의록 (기본)          : ~$0.24",
            "영어 → 한국어 (추천)          : ~$0.25",
            "고품질 gpt-4o-transcribe      : ~$0.43",
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
    print(f"  {c('I', 'key')}  pip install -r requirements.txt 실행")
    print(f"  {c('Enter', 'key')}  메인 메뉴로")
    print()
    ch = ask().upper()

    if ch == "C":
        cfg = BASE_DIR / "config.json"
        if not cfg.exists():
            shutil.copy(BASE_DIR / "config.example.json", cfg)
        subprocess.Popen(["notepad", str(cfg)])

    elif ch == "I":
        cls()
        print("\n  패키지 설치 중...\n")
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "-r",
             str(BASE_DIR / "requirements.txt")]
        )
        print()
        wait()

# ══════════════════════════════════════════════════════════════════
#  화면: 세션 수동 복구
# ══════════════════════════════════════════════════════════════════
def screen_recover_menu():
    while True:
        cls()
        print(banner(
            "세션 복구",
            "저장된 .jsonl 로그에서 회의록 재생성",
        ))
        print()

        jsonl_files = sorted(
            OUTPUT_DIR.glob("**/*.jsonl"),
            key=lambda f: f.stat().st_mtime,
            reverse=True,
        )

        if not jsonl_files:
            print(c("  .jsonl 세션 파일이 없습니다.\n", "warn"))
        else:
            print("  세션 로그 (최신순):\n")
            shown = jsonl_files[:10]
            for i, f in enumerate(shown, 1):
                try:
                    rel = f.relative_to(BASE_DIR)
                except ValueError:
                    rel = f
                print(f"  {c(i, 'num')}  {rel}")
            if len(jsonl_files) > 10:
                print(c(f"\n  ... 외 {len(jsonl_files) - 10}개", "dim"))
            print()

        print(ruler())
        print()
        print("  번호 또는 파일 전체 경로 입력.")
        print(f"  {c('Enter', 'key')} = 취소")
        print()
        ch = ask()
        if not ch:
            return

        if ch.isdigit() and jsonl_files:
            idx = int(ch) - 1
            if 0 <= idx < len(jsonl_files):
                rlog = str(jsonl_files[idx])
            else:
                print(c(f"\n  잘못된 번호: {ch}\n", "err"))
                wait()
                continue
        else:
            rlog = ch

        if not Path(rlog).exists():
            print(c(f"\n  파일 없음:\n  {rlog}\n", "err"))
            wait()
            continue

        cls()
        print("\n  저장된 세션에서 회의록 생성 중...\n")
        log.info("수동 복구: %s", rlog)
        _run_script(["--recover", rlog, "--email", "--output-dir", "output"])
        ACTIVE_SESSION.unlink(missing_ok=True)
        screen_done()
        return

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

    # 3. 이전 세션 미완료 체크
    prev_log, prev_time = _get_active_session()
    if prev_log:
        log.info("활성 세션 발견: %s", prev_log)
        screen_session_interrupted(prev_log, prev_time)

    # 4. PCM 백업 체크
    pcm_files = sorted(OUTPUT_DIR.glob("**/*_audio.pcm"))
    if pcm_files:
        log.info("PCM 백업 발견: %d개", len(pcm_files))
        screen_pcm_recovery()

    # 5. 메인 메뉴
    screen_main()


if __name__ == "__main__":
    main()
