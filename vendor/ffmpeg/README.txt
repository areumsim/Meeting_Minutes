vendor/ffmpeg/ — 번들용 ffmpeg 바이너리 위치
==================================================

이 폴더에 아래 파일을 넣으면 exe 빌드 시 함께 포함되어, 사용자가 ffmpeg를
따로 설치·PATH 등록하지 않아도 오디오 처리가 동작합니다.

  - ffmpeg.exe   (필수)
  - ffprobe.exe  (권장 — 오디오 길이 계산에 사용)

다운로드(Windows):
  https://www.gyan.dev/ffmpeg/builds/  → "release essentials" zip 안의
  bin/ffmpeg.exe, bin/ffprobe.exe 를 이 폴더에 복사.

동작 규칙(app_paths.py):
  - 앱은 이 번들 경로의 ffmpeg를 우선 사용합니다.
  - 이 폴더에 파일이 없으면 시스템 PATH의 ffmpeg를 fallback으로 찾습니다.
  - 둘 다 없으면 웹 UI가 "ffmpeg 없음"을 안내합니다(빌드 자체는 성공).

주의: 바이너리(*.exe)는 용량이 커서 git에는 커밋하지 않습니다(.gitignore 처리).
      배포 담당자가 빌드 전에 이 폴더에 직접 넣어 주세요.
