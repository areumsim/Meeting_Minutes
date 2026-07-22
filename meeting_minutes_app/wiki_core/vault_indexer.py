"""
vault_indexer.py — Obsidian Vault .md 파일 키워드 인덱싱 + TF-IDF 검색
=========================================================================
외부 의존성 없이 볼트의 마크다운 노트를 인덱싱하고 키워드 기반으로 검색한다.
wiki_ask.py 와 ingestion_pipeline.py 에서 관련 노트 찾기에 사용.

한국어: 음절 bigram (유니코드 가-힣 범위)으로 처리 — konlpy/mecab 불필요.
영어:  소문자 단어 토큰화.

CLI:
    python run_meeting.py vault-indexer --vault "D:\\Claude\\QC" --build
    python run_meeting.py vault-indexer --vault "D:\\Claude\\QC" --search "양자컴퓨팅"
"""

from __future__ import annotations

import os
import re
import json
import math
import glob
import time
import hashlib
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple, Sequence

try:
    from meeting_minutes_app.common import config_loader as _cfg
    _cfg_ok = True
except ImportError:
    _cfg = None  # type: ignore
    _cfg_ok = False


def _c(key: str, default: Any = None) -> Any:
    return _cfg.get(key, default) if _cfg_ok else default


# 한국어 조사·어미 bigram 제거용 불용어 (음절 bigram 레벨)
_KO_STOPWORD_BIGRAMS: set = {
    "이다", "있다", "없다", "하다", "되다", "이고", "하고", "이며", "하며",
    "에서", "에게", "부터", "까지", "으로", "로서", "에는", "이는", "그리",
    "고는", "지만", "는데", "는지", "으며", "이며", "이나", "이라", "이면",
    "한다", "합니", "니다", "습니", "입니", "했다", "했습", "있습", "없습",
}

_EN_STOPWORDS: set = {
    "the", "a", "an", "and", "or", "but", "in", "on", "at", "to", "for",
    "of", "with", "by", "from", "is", "are", "was", "were", "be", "been",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "that", "this", "these", "those", "it", "its",
    "he", "she", "they", "we", "you", "i", "my", "our", "your", "their",
    "not", "no", "up", "out", "as", "if", "so", "than", "then", "when",
    "which", "who", "what", "how", "all", "also", "more", "into", "about",
}

_KO_RANGE = re.compile(r'[가-힣]+')
_EN_WORD  = re.compile(r'[a-zA-Z0-9]+')
_WIKI_LINK = re.compile(r'\[\[([^\]|#]+)(?:[|#][^\]]*)?\]\]')
_FRONT_MATTER = re.compile(r'^---\s*\n.*?\n---\s*\n', re.DOTALL)
_MD_CLEAN = re.compile(
    r'```.*?```|`[^`]+`|!\[\[[^\]]+\]\]|#+\s|>\s|\*\*|__|\*|_|\|'
    r'|\[([^\]]+)\]\([^)]+\)',
    re.DOTALL
)


def _strip_frontmatter(text: str) -> Tuple[str, str]:
    """(frontmatter_block, body) 반환. frontmatter 없으면 ('', text)."""
    m = re.match(r'^---\s*\n(.*?)\n---\s*\n', text, re.DOTALL)
    if m:
        return m.group(0), text[m.end():]
    return "", text


def _strip_markdown(text: str) -> str:
    """마크다운 문법 제거 → 순수 텍스트."""
    fm, body = _strip_frontmatter(text)
    # 위키링크 타이틀만 남기기
    body = _WIKI_LINK.sub(r'\1', body)
    # 코드블록·인라인코드·이미지·헤더·인용·볼드·이탤릭·테이블 파이프 제거
    body = _MD_CLEAN.sub(r'\1', body)
    body = re.sub(r'\n{3,}', '\n\n', body)
    return body.strip()


def _tokenize(text: str) -> List[str]:
    """텍스트 → 토큰 리스트 (한국어 bigram + 영어 단어)."""
    tokens: List[str] = []
    # 위키링크 타이틀을 통째로 토큰화
    for wl in _WIKI_LINK.findall(text):
        tokens.extend(_tokenize_plain(wl))
    plain = _WIKI_LINK.sub(' ', text)
    tokens.extend(_tokenize_plain(plain))
    return tokens


def _tokenize_plain(text: str) -> List[str]:
    tokens: List[str] = []
    # 한국어 bigram
    for ko_run in _KO_RANGE.findall(text):
        chars = ko_run
        for i in range(len(chars) - 1):
            bg = chars[i:i+2]
            if bg not in _KO_STOPWORD_BIGRAMS:
                tokens.append(bg)
        # trigram도 추가 (더 정확한 매칭)
        for i in range(len(chars) - 2):
            tokens.append(chars[i:i+3])
    # 영어/숫자 단어
    for en in _EN_WORD.findall(text):
        w = en.lower()
        if len(w) > 1 and w not in _EN_STOPWORDS:
            tokens.append(w)
    return tokens


def _compute_tf(tokens: List[str]) -> Dict[str, float]:
    if not tokens:
        return {}
    freq: Dict[str, int] = {}
    for t in tokens:
        freq[t] = freq.get(t, 0) + 1
    total = len(tokens)
    return {t: c / total for t, c in freq.items()}


def _rrf_fuse(rankings: List[List[str]], k: int = 60) -> Dict[str, float]:
    """Reciprocal Rank Fusion (Cormack et al., 2009) — 여러 랭킹을 융합한다.

    score(d) = Σ_r 1 / (k + rank_r(d)).  k=60은 표준값으로 랭킹 상위 편향을 완화한다.
    """
    scores: Dict[str, float] = {}
    for ranking in rankings:
        for i, key in enumerate(ranking):
            scores[key] = scores.get(key, 0.0) + 1.0 / (k + i + 1)
    return scores


def _l2_normalize(vec: List[float]) -> List[float]:
    norm = math.sqrt(sum(x * x for x in vec)) or 1.0
    return [x / norm for x in vec]


def _dot(a: List[float], b: List[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


def _parse_sections(body: str) -> List[Dict[str, Any]]:
    """마크다운 body를 헤딩 단위로 분리. 헤딩 없으면 전체를 단일 섹션으로 반환."""
    sections: List[Dict[str, Any]] = []
    current_level, current_heading, current_lines = 0, "", []
    for line in body.splitlines():
        m = re.match(r'^(#{1,6})\s+(.*)', line)
        if m:
            if current_lines or current_heading:
                sections.append({
                    "level": current_level,
                    "heading": current_heading,
                    "content": "\n".join(current_lines).strip(),
                })
            current_level = len(m.group(1))
            current_heading = m.group(2).strip()
            current_lines = []
        else:
            current_lines.append(line)
    if current_lines or current_heading:
        sections.append({
            "level": current_level,
            "heading": current_heading,
            "content": "\n".join(current_lines).strip(),
        })
    return sections or [{"level": 0, "heading": "", "content": body.strip()}]


class VaultIndexer:
    """Obsidian Vault .md 파일을 TF-IDF로 인덱싱하고 검색한다."""

    def __init__(self, vault_path: str, index_path: str):
        self.vault_path = vault_path
        self.index_path = index_path
        self._notes: Dict[str, Dict] = {}   # rel_path → note_data
        self._idf: Dict[str, float] = {}
        self._built = False
        # 임베딩 하이브리드 검색 상태 (embedding_enabled=false면 전부 미사용)
        self._emb: Dict[str, Any] = {}       # {"model","dims","notes": {rel: {"h","v"}}}
        self._emb_loaded = False
        self._emb_client = None
        self._query_vec_cache: Dict[str, Optional[List[float]]] = {}

    @property
    def is_built(self) -> bool:
        return self._built

    # ── 빌드 / 로드 ─────────────────────────────────────────
    def build(self, verbose: bool = False) -> int:
        """볼트 전체 .md 파일을 읽어 인덱스를 빌드하고 저장한다."""
        if not self.vault_path or not os.path.isdir(self.vault_path):
            print(f"[indexer] 볼트 경로를 찾을 수 없음: {self.vault_path}")
            return 0

        raw: Dict[str, str] = {}  # rel_path → body text
        notes: Dict[str, Dict] = {}

        md_files = glob.glob(os.path.join(self.vault_path, "**", "*.md"), recursive=True)
        # 언더스코어로 시작하는 템플릿/인덱스 노트 제외 (false positive 방지)
        md_files = [f for f in md_files if not os.path.basename(f).startswith("_")]
        if verbose:
            print(f"[indexer] {len(md_files)}개 .md 파일 발견 (_시작 제외)")

        try:
            from meeting_minutes_app.wiki_core.obsidian import parse_frontmatter, safe_filename
        except ImportError:
            parse_frontmatter = _fallback_parse_frontmatter
            def safe_filename(n, max_len=80): return n[:max_len]

        for fpath in md_files:
            rel = os.path.relpath(fpath, self.vault_path).replace("\\", "/")
            try:
                content = open(fpath, encoding="utf-8", errors="replace").read()
            except Exception:
                continue
            meta, body = parse_frontmatter(content)
            title = str(meta.get("title") or "") or Path(fpath).stem
            plain_body = _strip_markdown(body)
            snippet = (plain_body[:200].replace("\n", " ")).strip()
            wikilink_title = safe_filename(title)
            raw[rel] = title + " " + plain_body
            notes[rel] = {
                "title": title,
                "wikilink_title": wikilink_title,
                "tags": meta.get("tags") or [],
                "date": str(meta.get("date") or ""),
                "type": str(meta.get("type") or ""),
                "snippet": snippet,
                "tf": {},
            }
            if _c("wiki_knowledge.section_index_enabled", True):
                raw_sections = _parse_sections(body)
                parsed_sections = []
                for sec in raw_sections:
                    sec_plain = _strip_markdown(sec["content"])
                    sec_tokens = _tokenize(sec["heading"] + " " + sec_plain)
                    parsed_sections.append({
                        "level": sec["level"],
                        "heading": sec["heading"],
                        "snippet": (sec_plain[:200].replace("\n", " ")).strip(),
                        "tf": _compute_tf(sec_tokens),
                    })
                notes[rel]["sections"] = parsed_sections

        # TF 계산
        tf_map: Dict[str, Dict[str, float]] = {}
        for rel, text in raw.items():
            tokens = _tokenize(text)
            tf = _compute_tf(tokens)
            tf_map[rel] = tf
            notes[rel]["tf"] = tf

        # IDF 계산
        N = max(len(notes), 1)
        df: Dict[str, int] = {}
        for tf in tf_map.values():
            for term in tf:
                df[term] = df.get(term, 0) + 1
        idf = {term: math.log(1 + N / (1 + cnt)) for term, cnt in df.items()}

        # 노트당 TF-IDF 상위 200개 용어만 저장 (인덱스 크기 절약)
        for rel in notes:
            tf = notes[rel]["tf"]
            tfidf = {t: tf[t] * idf.get(t, 0) for t in tf}
            top = sorted(tfidf.items(), key=lambda x: -x[1])[:200]
            notes[rel]["tf"] = dict(top)
            for sec in notes[rel].get("sections", []):
                sec_tfidf = {t: sec["tf"].get(t, 0) * idf.get(t, 0) for t in sec["tf"]}
                top_sec = sorted(sec_tfidf.items(), key=lambda x: -x[1])[:50]
                sec["tf"] = dict(top_sec)

        self._notes = notes
        self._idf = idf
        self._built = True

        self._save()
        if verbose:
            print(f"[indexer] 인덱싱 완료: {len(notes)}개 노트")

        # 임베딩 인덱스 증분 갱신 (실패해도 TF-IDF 인덱스에는 영향 없음)
        try:
            if self._emb_enabled():
                self.build_embeddings(verbose=verbose, texts=raw)
        except Exception as e:
            print(f"[indexer] 임베딩 빌드 실패 (무시): {e}")

        return len(notes)

    def load(self) -> bool:
        """저장된 인덱스를 로드한다. 없거나 실패 시 False."""
        if not self.vault_path or not os.path.isdir(self.vault_path):
            print(f"[indexer] 볼트 경로를 찾을 수 없음: {self.vault_path}")
            return False
        if not os.path.exists(self.index_path):
            return False
        try:
            with open(self.index_path, encoding="utf-8") as f:
                data = json.load(f)
            indexed_vault = str(data.get("vault_path") or "")
            if indexed_vault and _norm_path(indexed_vault) != _norm_path(self.vault_path):
                print(
                    "[indexer] 인덱스의 vault_path가 현재 설정과 다름 "
                    f"({indexed_vault} != {self.vault_path}) → 재인덱싱 필요"
                )
                return False
            # 7일 이상 지난 인덱스 경고 (새 노트가 검색에 반영되지 않을 수 있음)
            built_at_str = data.get("built_at", "")
            if built_at_str:
                try:
                    from datetime import datetime as _dt
                    built_at = _dt.fromisoformat(built_at_str)
                    age_days = (_dt.now() - built_at).days
                    if age_days >= 7:
                        print(
                            f"[indexer] ⚠️  인덱스가 {age_days}일 전에 빌드됨 "
                            "— 새 노트가 검색에 반영되지 않을 수 있습니다. "
                            "`run_meeting.py reindex` 로 갱신하세요."
                        )
                except Exception:
                    pass
            # TF-IDF/임베딩 인덱스가 서로 다른 시점에 빌드되면(standalone --embed 등)
            # 하이브리드 검색 결과가 어긋날 수 있음 — 하루 이상 차이 시 경고
            if self._emb_enabled() and os.path.exists(self.emb_path):
                try:
                    drift = abs(os.path.getmtime(self.index_path)
                                - os.path.getmtime(self.emb_path))
                    if drift > 86400:
                        print(
                            "[indexer] ⚠️  TF-IDF/임베딩 인덱스 빌드 시점이 하루 이상 어긋남 "
                            "— `run_meeting.py reindex` 로 함께 재빌드하세요."
                        )
                except Exception:
                    pass
            self._notes = data.get("notes", {})
            self._idf = data.get("idf", {})
            self._built = bool(self._notes)
            return self._built
        except Exception as e:
            print(f"[indexer] 인덱스 로드 실패: {e}")
            return False

    def reindex(self, verbose: bool = False) -> int:
        return self.build(verbose=verbose)

    def _save(self) -> None:
        os.makedirs(os.path.dirname(self.index_path) or ".", exist_ok=True)
        tmp = self.index_path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({
                    "built_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    "vault_path": self.vault_path,
                    "note_count": len(self._notes),
                    "notes": self._notes,
                    "idf": self._idf,
                }, f, ensure_ascii=False, separators=(",", ":"))
            os.replace(tmp, self.index_path)
        except Exception as e:
            print(f"[indexer] 저장 실패: {e}")

    # ── 임베딩 인덱스 (하이브리드 검색) ───────────────────────
    @property
    def emb_path(self) -> str:
        base, _ = os.path.splitext(self.index_path)
        return base + ".emb.json"

    @staticmethod
    def _emb_enabled() -> bool:
        return bool(_c("wiki_knowledge.embedding_enabled", False))

    def _emb_model(self) -> str:
        return str(_c("wiki_knowledge.embedding_model", "text-embedding-3-small"))

    def _emb_dims(self) -> int:
        return int(_c("wiki_knowledge.embedding_dims", 256) or 256)

    def _get_emb_client(self):
        if self._emb_client is not None:
            return self._emb_client
        api_key = ""
        if _cfg_ok:
            api_key = _cfg.get_api_key("api.openai_api_key", "OPENAI_API_KEY")
        if not api_key:
            api_key = str(_c("api.openai_api_key", "") or "")
        if not api_key:
            return None
        try:
            from openai import OpenAI
            if not bool(_c("ssl.verify", True)):  # meeting_minutes.SSL_VERIFY와 동일 키/기본값(안전 기본 True)
                try:
                    import httpx
                    import warnings
                    warnings.filterwarnings("ignore", message="Unverified HTTPS request")
                    self._emb_client = OpenAI(api_key=api_key, http_client=httpx.Client(verify=False))
                    return self._emb_client
                except ImportError:
                    pass
            self._emb_client = OpenAI(api_key=api_key)
        except Exception as e:
            print(f"[indexer] OpenAI 클라이언트 생성 실패 — 임베딩 검색 비활성: {e}")
            self._emb_client = None
        return self._emb_client

    def _embed_texts(self, texts: List[str]) -> Optional[List[Optional[List[float]]]]:
        """텍스트 목록을 임베딩한다. 실패 시 None (호출부는 TF-IDF로 폴백)."""
        client = self._get_emb_client()
        if client is None:
            return None
        try:
            resp = client.embeddings.create(
                model=self._emb_model(), input=texts, dimensions=self._emb_dims()
            )
            vecs: List[Optional[List[float]]] = [None] * len(texts)
            for item in resp.data:
                vecs[item.index] = _l2_normalize(list(item.embedding))
            return vecs
        except Exception as e:
            print(f"[indexer] 임베딩 호출 실패 (무시): {e}")
            return None

    def _load_embeddings(self) -> bool:
        if self._emb_loaded:
            return bool(self._emb.get("notes"))
        self._emb_loaded = True
        self._emb = {"notes": {}}
        try:
            if os.path.exists(self.emb_path):
                with open(self.emb_path, encoding="utf-8") as f:
                    data = json.load(f)
                if (data.get("model") == self._emb_model()
                        and int(data.get("dims") or 0) == self._emb_dims()):
                    self._emb = data
                else:
                    print("[indexer] 임베딩 인덱스 모델/차원 불일치 → `run_meeting.py reindex` 필요")
        except Exception as e:
            print(f"[indexer] 임베딩 인덱스 로드 실패 (무시): {e}")
        return bool(self._emb.get("notes"))

    def _save_embeddings(self) -> None:
        tmp = self.emb_path + ".tmp"
        try:
            os.makedirs(os.path.dirname(self.emb_path) or ".", exist_ok=True)
            payload = {
                "model": self._emb_model(),
                "dims": self._emb_dims(),
                "built_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "notes": self._emb.get("notes", {}),
            }
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
            os.replace(tmp, self.emb_path)
        except Exception as e:
            print(f"[indexer] 임베딩 인덱스 저장 실패 (무시): {e}")

    def build_embeddings(self, verbose: bool = False,
                         texts: Optional[Dict[str, str]] = None) -> int:
        """노트 임베딩 인덱스를 증분 빌드한다 (콘텐츠 해시로 변경분만 재임베딩).

        texts: rel_path → 원문 텍스트 (build()가 전달). 없으면 디스크에서 읽는다.
        반환: 새로 임베딩된 노트 수. 비활성/실패 시 0 — TF-IDF 검색은 영향 없음.
        """
        if not self._emb_enabled():
            return 0
        if not self._built and not self.load():
            return 0
        self._load_embeddings()
        model, dims = self._emb_model(), self._emb_dims()
        max_chars = int(_c("wiki_knowledge.embedding_max_chars", 4000) or 4000)

        existing: Dict[str, Dict] = self._emb.get("notes", {})
        pending: List[Tuple[str, str, str]] = []  # (rel, content_hash, text)
        for rel, note in self._notes.items():
            if texts is not None and rel in texts:
                body = texts[rel]
            else:
                content = self.get_note_content(rel) or ""
                body = note["title"] + "\n" + _strip_markdown(_FRONT_MATTER.sub("", content))
            body = (body or "").strip()[:max_chars]
            if not body:
                continue
            h = hashlib.sha1(f"{model}:{dims}:{body}".encode("utf-8", "ignore")).hexdigest()[:16]
            entry = existing.get(rel)
            if entry and entry.get("h") == h and entry.get("v"):
                continue
            pending.append((rel, h, body))

        # 볼트에서 삭제된 노트의 임베딩 제거
        for rel in list(existing.keys()):
            if rel not in self._notes:
                existing.pop(rel, None)

        done = 0
        if pending:
            if verbose:
                print(f"[indexer] 임베딩 대상: {len(pending)}개 노트 ({model}, {dims}차원)")
            BATCH = 64
            for i in range(0, len(pending), BATCH):
                chunk = pending[i:i + BATCH]
                vecs = self._embed_texts([t for _, _, t in chunk])
                if vecs is None:
                    print("[indexer] 임베딩 중단 — 완료분까지 저장 (TF-IDF 검색은 정상 동작)")
                    break
                for (rel, h, _), vec in zip(chunk, vecs):
                    if vec:
                        existing[rel] = {"h": h, "v": [round(x, 5) for x in vec]}
                        done += 1
                if verbose:
                    print(f"[indexer]   진행: {min(i + BATCH, len(pending))}/{len(pending)}")

        self._emb = {"model": model, "dims": dims, "notes": existing}
        self._save_embeddings()
        if verbose and done:
            print(f"[indexer] 임베딩 완료: +{done}개 (합계 {len(existing)}개)")
        return done

    def _semantic_ranking(self, query: str, limit: int,
                          path_prefixes: Optional[Tuple[str, ...]] = None) -> List[Tuple[str, float]]:
        """쿼리와 노트 임베딩의 코사인 유사도 랭킹. 비활성/실패 시 빈 리스트."""
        if not self._emb_enabled() or not self._load_embeddings():
            return []
        qkey = (query or "").strip()[:2000]
        if not qkey:
            return []
        if qkey in self._query_vec_cache:
            qvec = self._query_vec_cache[qkey]
        else:
            vecs = self._embed_texts([qkey])
            qvec = vecs[0] if vecs else None
            self._query_vec_cache[qkey] = qvec
        if not qvec:
            return []
        min_cos = float(_c("wiki_knowledge.embedding_min_cosine", 0.25) or 0.25)
        sims = [
            (rel, _dot(qvec, e["v"]))
            for rel, e in self._emb.get("notes", {}).items()
            if rel in self._notes and e.get("v")
            and (not path_prefixes or rel.startswith(path_prefixes))
        ]
        sims.sort(key=lambda x: -x[1])
        return [(rel, s) for rel, s in sims[:limit] if s >= min_cos]

    # ── 검색 ─────────────────────────────────────────────────
    def search(self, query: str, limit: int = 10,
              path_prefixes: Optional[Sequence[str]] = None) -> List[Dict[str, Any]]:
        """쿼리와 관련된 노트를 반환한다.

        기본은 TF-IDF 랭킹. wiki_knowledge.embedding_enabled=true이고 임베딩
        인덱스가 있으면 TF-IDF + 임베딩 코사인 랭킹을 RRF로 융합한다.
        결과의 "score"는 하위호환을 위해 항상 TF-IDF 점수이며, 융합 시
        "cosine"(임베딩 유사도)과 "rrf"(융합 점수) 필드가 추가된다.

        path_prefixes: 주어지면 이 접두사로 시작하는 노트만 검색 대상으로 삼는다
        (예: 특정 도메인 아카이브 + 01_References로 검색 범위 좁히기).
        vault_retrieval.detect_query_domain()/domain_search_prefixes() 참고.
        """
        if not self._built:
            if not self.load():
                return []
        query_tokens = _tokenize(query)
        if not query_tokens:
            return []
        allowed = tuple(p.rstrip("/") + "/" for p in path_prefixes) if path_prefixes else None
        scores: Dict[str, float] = {}
        for token in set(query_tokens):
            idf_val = self._idf.get(token, 0.0)
            if idf_val == 0:
                continue
            for rel, note in self._notes.items():
                if allowed and not rel.startswith(allowed):
                    continue
                tf_val = note["tf"].get(token, 0.0)
                if tf_val > 0:
                    scores[rel] = scores.get(rel, 0.0) + tf_val * idf_val

        candidates = max(limit * 3, limit)
        tfidf_ranked = sorted(scores.items(), key=lambda x: -x[1])[:candidates]
        sem_ranked = self._semantic_ranking(query, candidates, path_prefixes=allowed)

        fused: Dict[str, float] = {}
        cos_map: Dict[str, float] = {}
        if sem_ranked:
            fused = _rrf_fuse([
                [rel for rel, _ in tfidf_ranked],
                [rel for rel, _ in sem_ranked],
            ])
            cos_map = dict(sem_ranked)
            tf_map = dict(tfidf_ranked)
            ranked = [
                (rel, tf_map.get(rel, 0.0))
                for rel in sorted(fused, key=lambda r: -fused[r])[:limit]
            ]
        else:
            ranked = tfidf_ranked[:limit]

        results = []
        for rel, score in ranked:
            note = self._notes[rel]
            item = {
                "path": rel,
                "title": note["title"],
                "wikilink_title": note["wikilink_title"],
                "snippet": note["snippet"],
                "score": round(score, 4),
                "date": note["date"],
                "type": note["type"],
            }
            if fused:
                item["rrf"] = round(fused.get(rel, 0.0), 5)
                item["cosine"] = round(cos_map.get(rel, 0.0), 4)
            results.append(item)
        return results

    def find_related(self, text: str, limit: int = 5,
                     min_score: float = 0.05,
                     path_prefixes: Optional[Sequence[str]] = None) -> List[str]:
        """텍스트와 관련된 노트의 [[wiki link]] 타이틀 리스트를 반환한다.

        TF-IDF min_score 미달이어도 임베딩 유사도(embedding_min_cosine 이상)로
        검색된 노트는 유지한다 — 키워드가 겹치지 않는 의미적 관련 노트 회수용.
        path_prefixes: search() 참고.
        """
        results = self.search(text, limit=limit, path_prefixes=path_prefixes)
        return [
            r["wikilink_title"] for r in results
            if r["score"] >= min_score or r.get("cosine", 0.0) > 0.0
        ]

    def search_sections(self, query: str, limit: int = 10,
                        path_prefixes: Optional[Sequence[str]] = None) -> List[Dict[str, Any]]:
        """섹션 단위 TF-IDF 검색. section_index_enabled=true로 빌드된 인덱스 필요.
        path_prefixes: search() 참고."""
        if not self._built:
            if not self.load():
                return []
        query_tokens = _tokenize(query)
        if not query_tokens:
            return []
        allowed = tuple(p.rstrip("/") + "/" for p in path_prefixes) if path_prefixes else None
        scores: Dict[str, float] = {}
        for token in set(query_tokens):
            idf_val = self._idf.get(token, 0.0)
            if not idf_val:
                continue
            for rel, note in self._notes.items():
                if allowed and not rel.startswith(allowed):
                    continue
                for idx, sec in enumerate(note.get("sections", [])):
                    tf_val = sec["tf"].get(token, 0.0)
                    if tf_val > 0:
                        key = f"{rel}::{idx}"
                        scores[key] = scores.get(key, 0.0) + tf_val * idf_val
        ranked = sorted(scores.items(), key=lambda x: -x[1])[:limit]
        results = []
        for key, score in ranked:
            rel, idx_str = key.rsplit("::", 1)
            sec = self._notes[rel]["sections"][int(idx_str)]
            results.append({
                "note_path": rel,
                "note_title": self._notes[rel]["title"],
                "heading": sec["heading"],
                "level": sec["level"],
                "snippet": sec["snippet"],
                "score": round(score, 4),
            })
        return results

    def find_related_sections(self, text: str, limit: int = 10,
                              path_prefixes: Optional[Sequence[str]] = None) -> List[Dict[str, Any]]:
        """텍스트와 관련된 섹션 목록 반환. search_sections()의 편의 래퍼."""
        return self.search_sections(text[:500], limit=limit, path_prefixes=path_prefixes)

    def get_note_content(self, rel_path: str) -> Optional[str]:
        """볼트에서 노트 내용을 직접 읽는다 (Q&A 컨텍스트용)."""
        full = os.path.join(self.vault_path, rel_path.replace("/", os.sep))
        if not os.path.exists(full):
            return None
        try:
            return open(full, encoding="utf-8", errors="replace").read()
        except Exception:
            return None

    def get_section_content(self, rel_path: str, heading: str) -> Optional[str]:
        """노트에서 특정 heading 섹션의 전체(미절단) 본문을 반환한다.

        인덱스에는 200자 스니펫만 저장되므로, 근거로 쓸 전체 텍스트는
        디스크에서 다시 읽어 _parse_sections()로 재분리한다.
        일치하는 heading이 없으면 None (호출부는 whole-note로 폴백).
        """
        content = self.get_note_content(rel_path)
        if not content:
            return None
        try:
            from meeting_minutes_app.wiki_core.obsidian import parse_frontmatter
            _, body = parse_frontmatter(content)
        except ImportError:
            body = re.sub(r'^---\s*\n.*?\n---\s*\n', '', content, flags=re.DOTALL)
        for sec in _parse_sections(body):
            if sec["heading"] == heading:
                return sec["content"]
        return None

    # ── 팩토리 ────────────────────────────────────────────────
    @classmethod
    def from_config(cls) -> Optional["VaultIndexer"]:
        vault = _c("indexing.vault_path") or _c("obsidian.vault_path", "")
        index = _c("indexing.index_path", "data/vault_index.json")
        # 상대 경로는 config.json이 있는 프로젝트 루트 기준으로 해석 (실행 위치 무관)
        if index and not os.path.isabs(index) and _cfg_ok:
            root = getattr(_cfg, "_PROJECT_ROOT", None)
            if root:
                index = os.path.join(str(root), index)
        if not vault:
            try:
                from meeting_minutes_app.wiki_core.obsidian import _detect_obsidian_config
                detected = _detect_obsidian_config()
                vault = detected.get("vault_path", "")
                if vault:
                    print(f"[indexer] 볼트 경로 자동 감지: {vault}")
            except Exception:
                pass
        if not vault:
            return None
        return cls(vault_path=vault, index_path=index)


def _fallback_parse_frontmatter(content: str):
    """obsidian.py 없을 때 최소 frontmatter 파싱."""
    m = re.match(r'^---\s*\n(.*?)\n---\s*\n', content, re.DOTALL)
    if not m:
        return {}, content
    meta: Dict[str, Any] = {}
    for line in m.group(1).splitlines():
        if ":" in line and not line.strip().startswith("#"):
            k, _, v = line.partition(":")
            meta[k.strip()] = v.strip().strip('"')
    return meta, content[m.end():]


def _norm_path(path: str) -> str:
    """경로 비교용 정규화. 대소문자/슬래시 차이로 stale index를 놓치지 않게 한다."""
    return os.path.normcase(os.path.abspath(os.path.expanduser(path or "")))


def main():
    import argparse
    ap = argparse.ArgumentParser(description="Vault 인덱서")
    ap.add_argument("--vault", default=_c("indexing.vault_path") or _c("obsidian.vault_path", ""))
    ap.add_argument("--index", default=_c("indexing.index_path", "data/vault_index.json"))
    ap.add_argument("--build", action="store_true", help="인덱스 빌드")
    ap.add_argument("--embed", action="store_true",
                    help="임베딩 인덱스만 (증분) 빌드 — embedding_enabled=true 필요")
    ap.add_argument("--search", default="", help="검색 쿼리")
    ap.add_argument("--limit", type=int, default=10)
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    if not args.vault:
        print("오류: --vault 경로 필요 (또는 config.indexing.vault_path 설정)")
        return 1

    # 상대 index 경로는 프로젝트 루트 기준으로 해석 (from_config()와 동일 규칙)
    if args.index and not os.path.isabs(args.index) and _cfg_ok:
        root = getattr(_cfg, "_PROJECT_ROOT", None)
        if root:
            args.index = os.path.join(str(root), args.index)

    indexer = VaultIndexer(args.vault, args.index)

    if args.build:
        n = indexer.build(verbose=True)
        print(f"[indexer] 완료: {n}개 노트")

    if args.embed and not args.build:
        indexer.load()
        n = indexer.build_embeddings(verbose=True)
        print(f"[indexer] 임베딩 갱신: {n}개 노트")

    if args.search:
        if not args.build:
            indexer.load()
        results = indexer.search(args.search, limit=args.limit)
        if not results:
            print("[indexer] 결과 없음")
        for r in results:
            extra = f" cos={r['cosine']:.3f}" if "cosine" in r else ""
            print(f"  [{r['score']:.4f}{extra}] {r['title']}")
            if args.verbose:
                print(f"           {r['snippet'][:100]}")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main() or 0)
