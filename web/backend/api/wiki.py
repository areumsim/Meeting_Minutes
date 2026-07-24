"""
api/wiki.py — Vault Wiki 질의응답 API (읽기 전용)

CLI의 `meeting-minutes ask`(meeting_minutes_app.wiki_core.wiki_ask.WikiQA)를
그대로 재사용한다 — 별도 구현을 만들면 CLI/web이 서로 다른 로직으로 드리프트하기
쉽다는 걸 이번 리뷰에서 여러 번 확인했다.

노트 폴더(.md)의 로컬 검색 인덱스와 LLM 호출만 있으면 동작한다(Obsidian 앱/REST는
선택 — 있으면 검색 결과에 병합, 없어도 폴더 인덱스만으로 답변). 인덱스·LLM 호출이
서버(이 프로세스)에서만 가능하므로 백엔드가 떠 있는 배포 모드에서만 쓸 수 있다
(모바일 단독 배포에는 대응 기능 없음 — 프론트엔드에서 backendAvailable() 체크 후 노출).
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/wiki", tags=["wiki"])

_qa = None  # WikiQA는 LLM/Obsidian/인덱서 클라이언트를 들고 있어 요청마다 새로 만들지 않는다.


def _get_qa():
    global _qa
    if _qa is None:
        from meeting_minutes_app.wiki_core.wiki_ask import WikiQA
        _qa = WikiQA()
    return _qa


class WikiAskRequest(BaseModel):
    question: str
    max_notes: int = 0  # 0이면 WikiQA 기본값(config wiki.max_context_notes) 사용


@router.post("/ask")
def ask(req: WikiAskRequest):
    question = (req.question or "").strip()
    if not question:
        raise HTTPException(status_code=422, detail="question은 비어 있을 수 없습니다.")
    qa = _get_qa()
    try:
        return qa.ask(question, max_context_notes=req.max_notes)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Wiki 질의 실패: {e}")
