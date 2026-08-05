# REFERENCES & ROADMAP — 참고자료 · 기능 시너지 · 리팩토링

이 문서는 **회의록 자동화 + Obsidian 지식그래프 시스템**의 (1) 참고한 프로젝트·논문,
(2) 보완·확장 기술을 **기능별 시너지 묶음**으로, (3) 겹치는 후보의 교통정리, (4) 리팩토링
근거와 로드맵을 한곳에 모은 단일 문서다. (구 `REFERENCES.md` + `IMPROVEMENTS.md` 통합본)

- 작성 2026-08-04 · 갱신 2026-08-04(코드 실측 재확인) · 언어 한국어 · 근거: 리포 문서/코드 실측 + 웹 리서치(2026-08, URL 실측)
- 관련: [`검색랭킹_이론과근거.md`](검색랭킹_이론과근거.md) · [`기술검토_Natively_20260730.md`](기술검토_Natively_20260730.md) · [`참조아키텍처_로컬BYOK_회의록자동화.md`](참조아키텍처_로컬BYOK_회의록자동화.md) · [`prd/PRD_실시간관련정보_임베딩_PageIndex.md`](prd/PRD_실시간관련정보_임베딩_PageIndex.md) · [`prd/PRD_회의진행_페르소나에이전트_20260803.md`](prd/PRD_회의진행_페르소나에이전트_20260803.md)

## 채택상태 범례
| 태그 | 의미 |
|---|---|
| ✅ 채택 | 코드/문서에 반영됨 |
| 🟡 아이디어차용 | 프레임워크는 도입 안 하고 개념·기법만 |
| 🧪 채택검토 | 제약(로컬·BYOK·포터블·오프라인·저비용) 부합, 도입 후보 |
| 👀 관망 | 유망하나 지금 규모/제약엔 이르거나 무거움 |
| ⛔ 부적합 | 제약과 충돌 | ❌ 불채택 | 검토 후 도입 안 함 |

## 현 아키텍처 기준선 (보완 지점의 출발점)
| 컴포넌트 | 현재 | 위치 |
|---|---|---|
| 검색(어휘) | TF-IDF (한국어 bigram+trigram, 형태소분석기 미사용, tf·idf²) | `wiki_core/vault_indexer.py` |
| 검색(의미) | OpenAI `text-embedding-3-small`(256d) 코사인, 사전계산 | `wiki_knowledge.py`, `vault_index.json` |
| 융합 | RRF(k=60) + heading(#) 섹션 인덱스 | `vault_indexer._rrf_fuse`, `search_sections` |
| 지식그래프 | SQLite node/edge(회의·사람·조직·주제·결정·액션·노트) + Obsidian `[[위키링크]]` | `graph_db.py`, `graph_sync.py` |
| 크로스세션 메모리 | Supermemory 옵트인 팩트 메모리 | `supermemory_client.py` |
| STT | OpenAI Realtime/`gpt-4o-transcribe-diarize` → Groq → faster-whisper 폴백 | `stt.py`, `realtime_transcription.py`, `llm_client.py` |
| 회의 개입 | 페르소나 9종(트리아지 8 + 주기 요약 1) **구현됨** — 관찰 + 옆 카드(참견도 0~3). 위험 2종은 `hard_cap=2`로 화면 미개방 | `personas.py`, `facilitation.py` |
| 제약 | 로컬·BYOK·프라이버시, Windows 임베디드 파이썬 포터블, 오프라인 폴백, 저비용 | `참조아키텍처_로컬BYOK_…` |

---

# 1. 이미 참고한 것 (코드·문서에 반영)

## 1.1 오픈소스
| 프로젝트 | 링크 | 역할 | 위치/상태 |
|---|---|---|---|
| Natively | github.com/Natively-AI-assistant/natively-cluely-ai-assistant | **오디오 입력 계층만** 차용(시스템오디오+마이크 이중채널·원거리) | ✅(오디오)/❌기타. `기술검토_Natively`, 커밋 `d8d38c7`·`1edea69` |
| PageIndex | github.com/VectifyAI/PageIndex (MIT) | 트리(ToC)·"유사도≠관련성" **아이디어만** → heading 섹션 인덱스 | 🟡. `PRD_…_PageIndex.md` |
| turbovec | github.com/RyanCodrai/turbovec (MIT) | 벡터 규모 증가 시 로컬 저장(`data/*.tvim`) 후보 | 👀. `PRD_…_PageIndex.md` |
| Supermemory | github.com/supermemoryai/supermemory (MIT) | 크로스세션 팩트 메모리(옵트인, self-host·Ollama 오프라인) | ✅. `supermemory_client.py` |
| Obsidian Local REST API | github.com/coddingtonbear/obsidian-local-rest-api | 볼트 접근(127.0.0.1:27124)·발행 | ✅. `wiki_core/obsidian.py` |

## 1.2 이론·알고리즘
| 항목 | 출처 | 역할 | 위치 |
|---|---|---|---|
| RRF | Cormack·Clarke·Büttcher 2009 (SIGIR) | TF-IDF·임베딩 랭킹 융합(k=60) | ✅ `vault_indexer._rrf_fuse`, `검색랭킹 §3` |
| TF-IDF(길이정규화) | IR 표준 | 어휘 신호(한국어 n-gram) | ✅ `vault_indexer.py`, `검색랭킹 §2.1` |
| Dense 임베딩 코사인 | — | 의미 신호(256d) | ✅ `wiki_knowledge.py` |

## 1.3 학술 논문 — 회의 진행 페르소나 (`PRD_회의진행_페르소나에이전트 §18`)
| 논문 | ID/DOI | 역할 |
|---|---|---|
| Are We On Track? (CHI 2025) | arXiv 2504.01082 | 앰비언트 옆카드 vs 종료 후 상세 **역할 분담 근거** |
| LLM-Powered Devil's Advocate (ACM IUI) | 10.1145/3640543.3645199 | '악마의 변호인' 페르소나 |
| AI-Mediated Devil's Advocate | arXiv 2502.06251 | 소수의견 증폭 중재 |
| PersonaTeaming | arXiv 2605.05682 | 페르소나 레드팀 |

## 1.4 업계·시장
| 레퍼런스 | 출처 | 역할 |
|---|---|---|
| MS Teams AI 반발→끄기 토글 | windowslatest 2026-07-05, gHacks 2026-07-13 | 기본 OFF·즉시정지 설계 근거 |
| OpenAI Realtime Pricing 2026 | HackerNoon(4,000세션) | 시간 트리아지·전용 캡 정당화 |
| Otter·Fireflies | SaaS 회의록 | 로컬/BYOK vs SaaS 대비(`참조아키텍처 §5`) |

## 1.5 채택 중 스택
STT: OpenAI Realtime/`gpt-4o-transcribe-diarize`/`whisper-1` → **Groq**(`whisper-large-v3-turbo`) → **faster-whisper** · LLM: OpenAI/**Anthropic**(BYOK) · VAD: webrtcvad · MCP: **FastMCP** · 기타: FastAPI·sounddevice·watchdog·Send2Trash.

---

# 2. 보완·확장 — 기능별 시너지 클러스터

기술을 "축"이 아니라 **함께 맞물려 하나의 기능을 만드는 묶음(시너지)** 으로 정리한다.
각 클러스터: 목표 → 시너지 스택(무엇이 무엇을 가능케 하나) → 선행 리팩토링 → 우선.

## S1. 한국어 로컬 검색 강화 ★가장 큰 시너지
**목표**: OpenAI 없이도(오프라인) 한국어 검색 품질을 올린다. **여러 기법이 하나의 검색 파이프라인에 순차로 쌓여** 서로를 강화한다.

```
색인:   문서 → [Contextual Retrieval: 회의록 요약을 청크 맥락으로 prepend]
                → BGE-M3/KURE 임베딩(dense) + BGE-M3 sparse/BM25S(어휘)
검색:   쿼리 → dense검색 ┐
                → 어휘검색 ┘→ [RRF/가중융합] → [리랭커 재정렬] → [MMR 다양성] → [parent-doc 병합] → 결과
실행:   전부 FastEmbed(ONNX)로 torch 없이 로컬·오프라인
```

| 구성요소 | 링크 | 시너지 포인트 | 상태 |
|---|---|---|---|
| BM25S | github.com/xhluca/bm25s | TF-IDF 대체(어휘). **한국어 n-gram 토크나이저 그대로 재사용** | 🧪★ |
| KURE-v1 / BGE-M3 | hf.co/nlpai-lab/KURE-v1(MIT), hf.co/BAAI/bge-m3 | 의미(dense). **BGE-M3는 dense+sparse 동시 출력 → 어휘축도 겸함**(SPLADE 불필요) | 🧪★ |
| e5-small-ko | hf.co/dragonkue/multilingual-e5-small-ko-v2 | 384d 경량 티어(포터블) | 🧪 |
| FastEmbed | github.com/qdrant/fastembed | **torch 없이 위 임베딩·리랭커를 한 런타임에서** 로컬 구동 → 오프라인 성립 | 🧪★ |
| bge-reranker-v2-m3-ko / mxbai-rerank | hf.co/dragonkue/bge-reranker-v2-m3-ko, hf.co/mixedbread-ai/mxbai-rerank-base-v2 | RRF 상위 재정렬 → 한국어 정밀도 최종 보정 | 🧪★ |
| Contextual Retrieval | anthropic.com/engineering/contextual-retrieval | 색인 시점 1회. **회의록 기존 요약 재활용 → 추가 LLM 0** | 🧪 |
| Parent-Document/Auto-Merging | LlamaIndex | heading 트리와 동형. 작은 청크로 검색→상위 heading 병합 | 🧪★ |
| MMR | 개념 | 관련노트 N건 중복 억제(임베딩 코사인만) | 🟡 |
| RRF 가중/상대점수 융합 | opensearch RRF | 축별 가중 튜닝(코드만) | 🟡 |

**선행 리팩토링**: 검색을 stage 파이프라인화(§4 C3-a) + 임베딩 프로바이더 인터페이스화(§4 C3-b). **우선 ①**

## S2. 지식그래프를 검색·기억에 실제로 활용
**목표**: 지금은 "저장만" 하는 SQLite 그래프를 **검색 랭킹·결정 이력·자동 링크**에 활용. 세 기능이 **같은 그래프 위에서** 맞물린다.

| 구성요소 | 링크 | 시너지 포인트 | 상태 |
|---|---|---|---|
| Personalized PageRank | networkx(오프라인 무료) | 그래프 위 멀티홉 연상검색 → S1 결과와 융합(또 하나의 arm) | 🟡→🧪 |
| Graphiti **bi-temporal 모델** | github.com/getzep/graphiti (Apache-2.0) | edge에 `valid_at/invalid_at` → "결정 언제 뒤집혔나". **PPR·registry와 결합** | 🧪★(모델만, Neo4j 미도입) |
| A-MEM 자동링크 | github.com/agiresearch/A-mem, arXiv 2502.12110 | 새 노트 저장 시 관련 과거노트로 `[[링크]]` 자동 제안 → **그래프 밀도↑ → PPR 품질↑** | 🟡 |
| GraphRAG 커뮤니티요약 / LightRAG 이중레벨 | github.com/microsoft/graphrag, github.com/HKUDS/LightRAG | "주제 클러스터 자동 요약"(크로스-회의). LightRAG는 증분 갱신 | 🟡 |
| nano-graphrag | github.com/gusye1234/nano-graphrag | 위 기법을 자체 SQLite 그래프에 **소규모 이식**하는 참고 구현 | 🟡 |
| HippoRAG | github.com/OSU-NLP-Group/HippoRAG | PPR 연상검색의 이론·구현 레퍼런스 | 🟡 |

**선행 리팩토링**: 그래프 스키마 시간축 컬럼(§4 C3-d). **우선 ②**
**시너지 요약**: A-MEM이 링크를 늘리면 → 그래프가 촘촘해지고 → PPR 검색이 좋아지고 → bi-temporal이 그 위에 시간질의를 얹는다.

## S3. 크로스세션 기억(회의를 넘어 기억)
**목표**: 이전 회의를 기억해 prep-brief·사실검증 강화. Supermemory(현)를 유지하되 **시간축·프로파일**로 보강.

| 구성요소 | 링크 | 역할/시너지 | 상태 |
|---|---|---|---|
| Supermemory(현) | github.com/supermemoryai/supermemory (MIT) | 팩트 메모리 계층 유지(단일 바이너리·6767·Ollama 오프라인) | ✅ 유지 |
| Graphiti 시간축 | (S2와 동일) | 팩트에 유효기간 → **결정 이력**은 삭제 금지, 무효화로만 | 🧪 |
| 지속 인물/조직 프로파일 | Letta memory-block 패턴 (github.com/letta-ai/letta) | "이 사람과 지난 논의" 누적 → prep-brief 강화 | 🟡 |
| mem0 / Cognee | github.com/mem0ai/mem0, github.com/topoteretes/cognee | Supermemory 교체 시 오픈 대안(팩트추출·모순해소 / 임베디드 KG) | 🟡/👀 |

**우선 ②~③** · **주의**: Supermemory의 auto-forget(만료삭제)은 registry에 적용 금지.

## S4. 벡터 저장·화자 인프라 (하나의 저장소로 두 용도)
**목표**: `vault_index.json`을 견고한 저장소로 옮기고, **같은 저장소에 화자 임베딩(지문)도 축적**해 회의 간 화자 식별에 재사용.

| 구성요소 | 링크 | 시너지 포인트 | 상태 |
|---|---|---|---|
| sqlite-vec | github.com/asg017/sqlite-vec | 순수 C·Windows OK. 노트 임베딩 + **화자 지문**을 단일 `.db`에 | 🧪★ |
| ECAPA-TDNN 화자임베딩 | hf.co/speechbrain/spkrec-ecapa-voxceleb | 발화→화자벡터 → sqlite-vec에 축적 → **People 노드 자동 연결(S2)** | 🟡 |
| usearch / LanceDB | github.com/unum-cloud/usearch, github.com/lancedb/lancedb | 수만+ 확장 시 HNSW/컬럼형 경로 | 👀 |

**선행 리팩토링**: 벡터스토어 인터페이스화(§4 C3-c). **우선 ②**

## S5. 로컬 화자분리·STT
**목표**: `gpt-4o-transcribe-diarize`를 못 쓰는 오프라인/저비용 상황의 로컬 화자분리. **기존 faster-whisper 위에 최소침습**.

| 구성요소 | 링크 | 시너지 포인트 | 상태 |
|---|---|---|---|
| WhisperX | github.com/m-bain/whisperX | faster-whisper + 단어정렬 + pyannote를 묶음 → **현 폴백체인에 로컬 화자 단계** | 🧪★ |
| pyannote.audio | github.com/pyannote/pyannote-audio (코드 MIT) | 화자분리 엔진(모델 오프라인 번들 선행) | 🧪 |
| whisper.cpp | github.com/ggml-org/whisper.cpp (MIT) | torch 없는 초경량 오프라인 티어(화자품질 제한) | 🟡 |
| NeMo Sortformer / 3D-Speaker | hf.co/nvidia/diar_streaming_sortformer_4spk-v2, github.com/modelscope/3D-Speaker | 실시간/대안 엔진(GPU 전제) | 👀 |

**연결**: S5 화자분리 → S4 화자임베딩 축적 → S2 People 노드. **우선 ②**

## S6. 실시간 회의 개입 (차별점, 마지막)
**목표**: 볼트 근거 기반의 절제된 실시간 개입. 검색·게이팅이 받쳐줘야 성립.

| 구성요소 | 링크/근거 | 시너지 포인트 | 상태 |
|---|---|---|---|
| 페르소나 M0 관찰모드 | `PRD_회의진행_페르소나에이전트 §13` | 화면표시 없이 오탐률 수집 → 안전 도입 | ✅ 구현(2026-08-03) |
| 페르소나 M1 옆 카드 개입 | 같은 PRD §4·§8·§19 | 참견도 2·3 채널 + 확인/닫기 사람 라벨 | ✅ 구현(2026-08-04) |
| 논문 4편(§1.3) | arXiv/ACM | 역할분담·데블스애드버킷 설계 근거 | 🟡 |
| 중간 요약(브리핑 트랙 A) | `PRD_회의중_음성브리핑` FR-A2 | 주기 페르소나 `summarizer` 1종으로 합쳐 구현 | ✅ 구현(2026-08-04) |
| 음성 출력(TTS, 트랙 C) | 같은 PRD | 브라우저 TTS(비용 0). 참견도 4·5 — `max_level` 3이 막는다 | 🟡 미착수(M3) |
| 라이브 웹검색 근거 | 같은 PRD 트랙 B | 팩트체커 개입의 `searched` 는 현재 항상 False | 🟡 미착수(M2) |
| 검색 신뢰도 게이팅(CRAG 경량) | arXiv 2401.15884 | 저신뢰 시 "근거 불충분" → 개입 억제 | 🟡 |

> M2(위험 페르소나 화면 개방)의 전제는 여전히 **오탐률 실측**이다. 팩트체커·비판자는
> `personas.hard_cap=2` 로 설정으로도 자동 표시까지 올릴 수 없고, 실측 데이터는
> 관찰 로그 + 카드의 확인/닫기 사람 라벨 + `facilitation-report --replay` 로 모은다.

**선행 리팩토링**: realtime.py 전송/도메인 분리(§4 C3-e). **우선 ③**

---

# 3. 겹치는 항목 정리 (교통정리)

같은 목적을 두고 **경쟁·중복**하는 후보들. "무엇을 쓰고 나머지는 언제"만 남긴다.

| 목적 | 겹치는 후보 | 권장 선택 | 나머지는 언제 |
|---|---|---|---|
| **벡터 저장소** | json(현)·sqlite-vec·turbovec·LanceDB·usearch·FAISS·Qdrant·Chroma·Milvus·pgvector | **sqlite-vec**(수천~수만) | 수십만+ HNSW=usearch/LanceDB · turbovec=압축 필요 시 · 서버형(Qdrant/pg/Milvus)은 배포모델 바뀔 때(Milvus=Windows 미지원) |
| **크로스세션 메모리** | Supermemory·mem0·Cognee·Letta·MemoryOS·Graphiti | **Supermemory 유지 + Graphiti 시간축 차용** | mem0/Cognee=Supermemory 교체 시 · Letta=프로파일 self-edit · MemoryOS=승격개념. **역할이 다름**: 팩트store(Supermemory/mem0) vs 시간축KG(Graphiti) vs 파이프라인(Cognee) |
| **로컬 임베딩** | KURE-v1·BGE-M3·e5-small-ko·Qwen3-Emb·nomic·GTE·Jina-v3 | **KURE-v1(품질)/e5-small-ko(경량)** | BGE-M3=하이브리드 필요 시 · Qwen3=품질상한 벤치 · Jina-v3는 CC-BY-NC 주의 |
| **리랭커** | bge-reranker-ko·mxbai·Qwen3-rerank·Jina-v2·Cohere·MS-MARCO | **bge-reranker-v2-m3-ko / mxbai(Apache)** | Cohere=API·외부전송(부적합) · Jina-v2=CC-BY-NC · MS-MARCO=영어중심(부적합) |
| **청크 맥락 보존** | Contextual Retrieval·Late Chunking·Semantic Chunking | **Contextual Retrieval**(요약 재활용) | Late Chunking=로컬 임베딩 전환 시 · Semantic=heading 없는 대형 노트에서만 |
| **실행 런타임** | FastEmbed·sentence-transformers·llama.cpp·Ollama | **FastEmbed(ONNX)** | ST=torch 감수 시 · llama.cpp/Ollama=별도 프로세스라 포터블 마찰 |
| **그래프RAG** | GraphRAG·LightRAG·nano-graphrag·HippoRAG·txtai·Cognee | **아이디어차용**(nano-graphrag 참고 이식) | 프레임워크 통째 도입은 저비용·포터블과 충돌 → 기법만 |
| **그래프 검색축** | PPR(HippoRAG)·GraphReader·커뮤니티요약 | **PPR(networkx)** | GraphReader=에이전트 반복(비용 큼, 관망) |
| **Obsidian 관련노트 추천** | Smart Connections vs 자체 임베딩검색 | **자체 검색 유지 + Smart Connections는 사람용 보조** | 기능 중복 — 백엔드는 자체, UI 추천은 플러그인 |
| **Obsidian 그래프 뷰/관계** | Excalibrain·Breadcrumbs·Juggl·Graph Analysis·Dataview | **Dataview(전제)+Breadcrumbs(관계타입)+Graph Analysis(링크예측)** | Excalibrain/Juggl=시각탐색 필요 시 |
| **화자분리** | WhisperX·pyannote·whisper.cpp·NeMo·3D-Speaker·Deepgram | **WhisperX(+pyannote)** | whisper.cpp=torch 배제 초경량 · NeMo=GPU 실시간 · Deepgram=외부전송(부적합) |
| **쿼리 확장** | HyDE·RAG-Fusion vs 비-LLM 한국어 정규화 | **비-LLM 한국어 정규화** | HyDE/RAG-Fusion=쿼리당 LLM(지연·오프라인 충돌) |

> **GBrain(사용자 지목)** ⚠️: Garry Tan의 `github.com/garrytan/gbrain`(+Obsidian 포트 `github.com/joedanz/pbrain`)이 유력. **RRF+BM25+벡터 하이브리드·무LLM 타입드 엣지 자동생성** 철학이 이 시스템과 동일 → S2의 아이디어 소스. TS/Postgres 스택이라 이식은 개념만. 다른 도구를 의도했다면 교체 필요.

---

# 4. 리팩토링 근거

## C-0. 정직한 현황 — 대표 복사-드리프트는 이미 수렴
CLAUDE.md가 경고하던 중복은 실측상 대부분 단일 소스로 수렴됨:
- 단가: `common/pricing.py` 단일 소스, `realtime_transcription.py`·`run_realtime.py`·`api/tools.py`는 `pricing.stt_rate_per_min()` **import**(로컬 표 제거 확인).
- 경로: `trash.py:_resolve()`가 `app_paths.get_base_dir()` 사용(FR-001, 고아폴더 거짓보고 결함 제거).
- `PRD §08-03`이 지적한 "realtime.py spend_guard 0건"도 **현재 8건으로 반영됨**.

**단, "수렴됐다"가 "끝났다"는 아니다** — 2026-08-04 리뷰에서 같은 패턴의 신규 갈라짐
4건이 나왔고 전부 수정됐다. 어느 것도 표를 복사한 게 아니라 **판정 규칙이 두 어휘·두
목록으로 갈라진** 경우다:

| 갈라진 것 | 증상 | 수렴 지점 |
|---|---|---|
| `sessions.source`(DB 어휘) vs finalize `SessionInputs.source` | 웹 실시간이 웹 업로드와 같은 `"web"` 이라 실시간 판정 실패 → 상세 화면 STT 가 실제의 1/3 | `pricing.is_realtime_session()`(mode 기준) + `resolve_two_pass()`(기록 우선) |
| 설정값 `realtime.two_pass` vs 런타임 `self._two_pass` | 순수 WS 세션에 돌지도 않은 보정 요금 부과 | `sessions.stt_two_pass` 컬럼에 런타임 값 기록 |
| 세션 과금 kind 를 호출부가 손으로 열거 | `web_research` 가 회의 상세에서 누락 | `usage_log.session_spend_by_kind()` |
| kind→라벨 표가 프런트 2곳 | 한쪽에만 kind 추가되는 시작점 | `web/frontend/src/lib/costKinds.ts` |
| 콘솔 UTF-8 블록이 8개 파일에 3가지 철자 | `meeting_minutes.py` 만 `getattr` 가드가 없어 `pythonw`(stdout=None)에서 import 실패 | `common/console.py` |
| `_resolve_db_path()` 가 2곳에 문자 단위 동일 | (아직 갈라지진 않음) | `common/sqlite_util.resolve_db_path` |

**고치지 않기로 한 것**: `def _c(key, default)` config 접근자가 17개 파일에 있다.
2줄짜리이고 변형이 실제로 다르다(모듈 로드 시 `_cfg_ok` 플래그 vs 호출마다
`try/except`) — 후자는 `config_loader` import 실패까지 흡수한다. 합치려면 17개
호출부의 실패 의미를 하나로 정해야 하는데, 그 판단 근거가 될 사고 이력이 없다.
**갈라져서 사고가 난 적이 없는 중복은 지금 구조 신호가 아니다** — 근거가 생기면 그때
합친다(이 리포의 '수정 필요성 더블체크' 규율).

→ 아래는 "미해결 버그"가 아니라 **구조 부채·예방·로드맵 확장점**이다.

## C-1. 측정된 부채 — 대형 파일(관심사 혼재) `wc -l 2026-08-04 재실측`
| 파일 | 줄 | 혼재 책임 | 분리 제안 |
|---|---|---|---|
| `realtime_transcription.py` | 2,424 | 오디오캡처·VAD·STT·2pass·비용·세션상태 | STT클라이언트/VAD/세션/과금 분리 |
| `web/backend/api/realtime.py` | 2,122 | WS·HTTP폴백·검색·페르소나·spend_guard | 전송 ↔ 도메인 분리 |
| `wiki_core/facilitation.py` | 1,884 | 트리아지·개입생성·중간요약·관찰로그·리플레이·CLI | 로그/리포트 ↔ 오케스트레이터 분리 |
| `wiki_knowledge.py` | 1,512 | brief·registry·context·reindex | 서브모듈 분리 |
| `vault_indexer.py` | 1,313 | 토큰화·TF-IDF·임베딩·RRF·섹션·검색 | **인덱싱 ↔ 검색(랭킹/융합) 분리** |
| `meeting_workflow.py` | 1,240 | 오케스트레이션·claim verify·분류 | 검증 로직 분리 |
| `obsidian.py` | 1,158 | FS·REST·발행·링크갱신 | 접근 ↔ 발행 분리 |

> 이 표는 **재실측할 때만** 갱신한다. 초판이 `api/realtime.py` 를 1,965 로 적어 둔
> 사이 파일은 이미 2,089 였다(M1 커밋 반영 누락) — 부채 표가 낡으면 "무엇이 가장
> 급한가"의 순서 자체가 틀린다.

**근거**: 신규 기능(리랭커·페르소나·오프라인) 삽입점이 큰 파일 깊숙이 있어 회귀 위험↑. 리포에 회귀 사례가 이미 문서화됨.

## C-2. 반복 실패 패턴 — "같은 판정이 두 곳에 있으면 갈라진다"
단가 4곳·노트판정 2곳·경로 2곳이 복사됐다 갈라져 사고(워처 과금 누락·고아 폴더 거짓보고).
여기에 C-0 의 신규 4건이 더해져 **7회 이상 반복** = 구조 신호.

신규 4건이 보여준 것은 복사만이 원인이 아니라는 점이다. **같은 이름의 필드가 두 어휘를
담거나(`source`), 설정값과 런타임값이 같은 뜻인 척하거나(`two_pass`), 목록을 호출부가
열거하면(kind)** 복사 없이도 갈라진다. 판정을 함수 하나로 노출하는 것만으로는 부족하고,
**그 함수가 받는 입력이 한 어휘인지**까지 봐야 한다 — `is_two_pass_source(source)` 는
단일 함수였지만 유일한 호출부가 다른 어휘를 먹이고 있었다.

- 제안: 단일소스 규칙을 **공유 모듈 + 얇은 어댑터**로만 노출하고 **직접 재구현을 테스트로 차단**(예: "pricing 표를 밖에서 정의하면 실패", "노트 판정은 `iter_note_files`만"). `iter_note_files()`가 좋은 선례 — 경로·과금 집계에도 같은 규율.
- 추가 제안: **"기록 우선, 추정은 폴백"**. 지난 세션을 다시 계산하는 화면은 그때의 설정이
  아니라 그때 남긴 값을 읽는다(`sessions.stt_two_pass` 선례). 설정은 미래에만 적용된다.
- 추가 제안: **비용 게이트는 "볼 사람이 있는가"를 먼저 묻는다.** `facilitation` 이 같은
  판정을 네 곳(채널 없음·mute·예산 소진·참견도 미달)에 두고 있는데, 새 생성 경로가
  그중 하나를 빠뜨려 사고가 났다(요약이 리플레이 견적을 넘김). 새 LLM 호출 경로를
  추가할 때 이 네 게이트를 지나는지 확인한다.

## C-3. 로드맵을 얹기 위한 확장점 (이게 "왜 지금 리팩토링" 핵심)
| 리팩토링 | 근거 | 무엇을 가능케 | 영향 파일 | 연결 |
|---|---|---|---|---|
| a. **검색 stage 파이프라인화** (retrieve→fuse→rerank→MMR→parent-merge) | `search()`가 융합까지 인라인 → 삽입점 없음 | S1 전체 | `vault_indexer.py`, `realtime_search.py` | S1 |
| b. **임베딩 프로바이더 인터페이스화** (OpenAI↔로컬) | `embedding_enabled`가 API 전제, 폴백 TF-IDF뿐 | S1 오프라인·KURE 스왑 | `wiki_knowledge.py` | S1 |
| c. **벡터스토어 인터페이스화** (json↔sqlite-vec) | 벡터가 json에 직결 | S4 전환·화자지문 공용화 | `vault_indexer.py` | S4 |
| d. **그래프 시간축 컬럼** (edge valid_at/invalid_at) | edge에 유효기간 없음 | S2·S3 결정이력 | `graph_db.py`, `graph_sync.py` | S2·S3 |
| e. **전송/도메인 분리** (realtime WS·HTTP ↔ 전사·검색·개입) | 1,965줄 혼재 | S6 페르소나·S1 리랭커 안전 삽입 | `api/realtime.py` | S6 |
| f. STT는 이미 폴백체인 추상화 양호 → **화자분리 레이어만 추가** | — | S5 | `stt.py` | S5 |

> 원칙: **큰 프레임워크 통째 도입 금지 — 확장점(인터페이스)만 열고 기법을 이식.** 포터블·오프라인·저비용 유지.

---

# 5. 통합 로드맵 (기능 × 리팩토링)

| 단계 | 선행 리팩토링 | 얹는 시너지 클러스터 | 효과 |
|---|---|---|---|
| **①즉시 (저비용·로컬)** | C3-a 검색 stage화 · C3-b 임베딩 인터페이스 | **S1**(BM25S·KURE/BGE-M3·FastEmbed·리랭커·MMR·parent-doc·contextual) | 한국어 검색 품질↑, OpenAI 종속·오프라인 해소, 비용↓ |
| **②중기 (그래프·기억·화자)** | C3-c 벡터스토어 · C3-d 시간축 · C3-f 화자레이어 · C3-e 전송분리 | **S2**(PPR·bi-temporal·자동링크) · **S3**(프로파일) · **S4**(화자지문) · **S5**(WhisperX) | 그래프를 검색·기억에 실제 활용, 오프라인 화자분리 |
| **③장기 (실시간 개입)** | 대형 파일 책임 분리 완료 | **S6**(페르소나 M0·음성브리핑·게이팅) · 크로스-회의 요약 | 볼트 근거 기반 실시간 개입(차별점) |

**한 줄 결론**: **검색 파이프라인 stage화 + 임베딩/벡터스토어 인터페이스화(①)** 가 리랭커·로컬임베딩·오프라인모드를 저위험으로 열고, **그래프 시간축·전송분리(②)** 가 결정이력·PPR·화자식별·페르소나 개입(③)의 토대가 된다. 리팩토링은 목적이 아니라 **로드맵을 얹기 위한 확장점 확보**다.

---

# 부록. 검증 메모
- 대형 파일 줄 수·spend_guard(realtime.py 8건)·pricing import·`trash._resolve`는 2026-08-04 실측.
- **미해결(측정 대기) — 세션 비용 추정의 LLM 항이 체계적 과소평가다.**
  `finalize.run_post_session` 은 LLM 단계를 8개 지나는데(context·refine·minutes·
  actions·claim_verify·summary·publish enrich·wiki_proposal, 일부는 내부 배치 반복)
  `estimate_session_cost` 의 `minutes` 항은 `minutes_cost()` **1회분**만 더한다.
  배수를 지어 넣지 않은 이유는 단계별 입력 크기가 크게 다르고 실측이 없기 때문이다
  (근거 없는 상수 = 이 리포가 금지한 휴리스틱). 재교정 경로: `llm_client` 가 응답의
  `usage` 를 누적하게 바꿔 실사용 토큰을 모은 뒤 단계별 상수를 정한다.
  영향: 월 한도가 실제보다 늦게 걸린다(회의록 모델이 비쌀수록 오차가 크다).
- 2026-08-05 재실측: 테스트 `1092 passed, 1 skipped`(pytest) · `102 passed`(vitest) ·
  `constraints-web.txt` 고정 52건. 이 문서가 수치를 인용할 때는 **실행한 날**을 함께 적는다.
- 라이선스 비상업(CC-BY-NC): Jina Reranker v2 base, Jina embeddings v3 → 사내 배포 시 확인.
- turbovec: MIT 확인, `win_amd64` 휠·AVX2 하한 실물 확인 필요. Milvus Lite: Windows 미지원(issue #169). Zep CE deprecated → 실체는 Graphiti(기본 Neo4j).
- 자체 벤치 주의: PageIndex FinanceBench 98.7%, Supermemory/mem0 벤치는 자체 주장(독립검증 별도).
- 미검증: txtai·MemoryScope·A-MEM 라이선스, KURE의 FastEmbed 기본지원(BGE-M3 아키텍처라 ONNX 변환 가능), usearch/LanceDB cp39-abi3-win 휠 실물.
- GBrain 정체는 §3 각주 참조 — 사용자 의도 재확인 권장.
