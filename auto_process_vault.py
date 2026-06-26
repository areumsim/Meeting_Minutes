# -*- coding: utf-8 -*-
"""
auto_process_vault.py - 볼트의 새 녹음을 자동 일괄 처리 (batch process)
=======================================================================
옵시디언 볼트를 훑어 '아직 처리하지 않은' 녹음을 찾아
    meeting_assistant.py process "<파일>" --notify email
로 돌린다. process 모드라 전체 산출물 저장 + Obsidian 발행 + 계획 매칭/병합 + 메일까지 수행.

중복 방지: output/ 에 그 파일 이름을 포함하고 segments.json 이 있는 폴더가 있으면 skip.
첫 실행 시 오래된 파일까지 한꺼번에 처리하지 않도록 최근 N일 + 최소 크기 필터 적용.

Windows 작업 스케줄러에 run_auto_process.bat 를 등록하면 주기적으로 자동 실행됨.
환경이 다르면 아래 설정값만 수정하세요.
"""
import os
import sys
import glob
import subprocess
import datetime

# ── 설정 ─────────────────────────────────────────────────────
# VAULT: config.json의 obsidian.vault_path 를 우선 사용, 없으면 환경변수 OBSIDIAN_VAULT
try:
    import config_loader as _cfg
    VAULT = _cfg.get("obsidian.vault_path", "") or os.environ.get("OBSIDIAN_VAULT", "")
except ImportError:
    VAULT = os.environ.get("OBSIDIAN_VAULT", "")

NOTIFY = "email"                  # 처리 후 발송: "email" 또는 "" (발송 안 함)
MAX_AGE_DAYS = 14                 # 최근 N일 이내 녹음만 (첫 실행 시 과거 전체 처리 방지)
MIN_SIZE_MB = 0.5                 # 이 크기 미만(짧은 테스트 녹음 등)은 건너뜀
AUDIO_EXTS = (".webm", ".m4a", ".mp3", ".wav", ".ogg", ".mp4", ".mpga", ".flac")
# ─────────────────────────────────────────────────────────────

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "output")
LOG = os.path.join(HERE, "auto_process.log")


def log(msg):
    line = "[{}] {}".format(datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), msg)
    print(line)
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def already_processed(stem):
    """output/ 에 stem 을 포함하고 segments.json 이 있는 폴더가 있으면 처리 완료."""
    if not os.path.isdir(OUT):
        return False
    for d in os.listdir(OUT):
        full = os.path.join(OUT, d)
        if os.path.isdir(full) and stem in d:
            if glob.glob(os.path.join(full, "*segments.json")):
                return True
    return False


def find_audio(vault):
    out = []
    for p in glob.glob(os.path.join(vault, "**", "*"), recursive=True):
        if os.path.isfile(p) and os.path.splitext(p)[1].lower() in AUDIO_EXTS:
            out.append(p)
    return out


def main():
    if not VAULT:
        log("볼트 경로 미설정 — config.json의 obsidian.vault_path 또는 환경변수 OBSIDIAN_VAULT를 설정하세요.")
        return 2
    if not os.path.isdir(VAULT):
        log("볼트 경로 없음: {} — config.json의 obsidian.vault_path를 확인하세요.".format(VAULT))
        return 2

    now = datetime.datetime.now()
    audios = find_audio(VAULT)
    new = []
    for p in audios:
        stem = os.path.splitext(os.path.basename(p))[0]
        if already_processed(stem):
            continue
        try:
            size_mb = os.path.getsize(p) / (1024 * 1024)
            age_days = (now - datetime.datetime.fromtimestamp(os.path.getmtime(p))).days
        except OSError:
            continue
        if size_mb < MIN_SIZE_MB:
            log("skip(작음 {:.2f}MB): {}".format(size_mb, os.path.basename(p)))
            continue
        if age_days > MAX_AGE_DAYS:
            log("skip(오래됨 {}일): {}".format(age_days, os.path.basename(p)))
            continue
        new.append(p)

    log("녹음 {}개 발견, 처리 대상 신규 {}개".format(len(audios), len(new)))
    done = 0
    for p in new:
        log("처리 시작: {}".format(os.path.basename(p)))
        cmd = [sys.executable, os.path.join(HERE, "meeting_assistant.py"), "process", p]
        if NOTIFY:
            cmd += ["--notify", NOTIFY]
        rc = subprocess.call(cmd, cwd=HERE)
        if rc == 0:
            done += 1
            log("완료: {}".format(os.path.basename(p)))
        else:
            log("실패(rc={}): {}".format(rc, os.path.basename(p)))
    log("끝. 신규 처리 {}건".format(done))
    return 0


if __name__ == "__main__":
    sys.exit(main())
