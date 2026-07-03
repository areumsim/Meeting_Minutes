# 메일 발신을 회사 Outlook(Microsoft 365)로 전환

현재는 Gmail(`sar10320@gmail.com`)에서 보내 회사메일(`you@example.com`)로 수신 중입니다.
발신을 회사 Outlook으로 바꾸려면 아래 한 블록만 교체하면 됩니다.

> ⚠ 비밀번호는 보안상 본인만 입력하세요. (이 자료에는 자리표시자만 둡니다.)

## 1) `config.json` 의 `email` 블록 교체

```json
  "email": {
    "sender":    "you@example.com",
    "password":  "<여기에 Outlook 앱 비밀번호 입력>",
    "recipient": "you@example.com",
    "smtp_host": "smtp.office365.com",
    "smtp_port": 587
  },
```

- `recipient` 는 쉼표로 여러 명 지정 가능 (예: `"you@example.com, 팀장@hanbit.com"`).
- 기존 Gmail 설정으로 되돌릴 수 있게, 바꾸기 전 원래 `email` 블록을 메모해두면 좋습니다.

## 2) 회사 테넌트 사전 확인 (중요)

회사 Microsoft 365가 **SMTP AUTH(기본 인증)** 를 막아두면 로그인이 실패합니다. 이 경우 IT에 요청:

- "내 계정 **SMTP AUTH 허용**" 또는
- "**앱 비밀번호(App password)** 발급" (MFA 사용 시 일반 비밀번호 대신 앱 비밀번호 필요)

막혀 있고 풀기 어려우면 → **지금처럼 Gmail 발신 → 회사메일 수신** 유지가 가장 안전합니다.

## 3) 테스트

교체·저장 후, 앱 폴더에서 새 녹음 하나로 확인:

```
python run_meeting.py batch "D:\Claude\QC\<테스트 녹음>.m4a" --notify email
```

메일이 `you@example.com` 으로 도착하면 성공. 실패 메시지(예: `535 5.7.139 Authentication unsuccessful`)가 뜨면 SMTP AUTH/앱 비밀번호 문제이니 위 2)로.

참고: `process`는 호환 명령으로 남아 있지만, 새 문서와 테스트는 `batch` 기준으로 작성합니다. 메일 첨부에는 회의록/요약뿐 아니라 설정과 경로에 따라 사실검증, Wiki Context/Proposal 산출물이 함께 생성됩니다.
