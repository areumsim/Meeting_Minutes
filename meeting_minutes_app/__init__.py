"""Implementation modules for the Meeting Minutes project."""

#: 앱 버전의 **단일 소스**. pyproject.toml 이 이 값을 읽어간다
#: ([tool.setuptools.dynamic] version = {attr = "meeting_minutes_app.__version__"}).
#:
#: 리터럴을 코드 쪽에 두는 이유: 정본 배포본(포터블 = 임베디드 파이썬 + 소스 복사)에는
#: dist-info 가 없어 importlib.metadata.version() 이 **항상** 실패한다. 반대 방향
#: (코드가 pyproject 를 읽음)도 불가능하다 — 배포본에 pyproject.toml 이 없다.
#: 읽을 때는 common/version.py 의 app_version() 을 쓴다.
__version__ = "0.1.0"
