"""
vault_indexer.py — Obsidian Vault .md 파일 키워드 인덱싱 + TF-IDF 검색
=========================================================================
외부 의존성 없이 볼트의 마크다운 노트를 인덱싱하고 키워드 기반으로 검색한다.
wiki_ask.py 와 ingestion_pipeline.py 에서 관련 노트 찾기에 사용.

한국어: 음절 bigram (유니코드 가-힣 범위)으로 처리 — konlpy/mecab 불필요.
영어:  소문자 단어 토큰화.

CLI:
    python vault_indexer.py --vault "D:\\Obsidian\\MyVault" --build
    python vault_indexer.py --vault "D:\\Obsidian\\MyVault" --search "양자컴퓨팅"
"""

from __future__ import annotations

import os
import re
import json
import math
import glob
import time
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple

try:
    import config_loader as _cfg
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


class VaultIndexer:
    """Obsidian Vault .md 파일을 TF-IDF로 인덱싱하고 검색한다."""

    def __init__(self, vault_path: str, index_path: str):
        self.vault_path = vault_path
        self.index_path = index_path
        self._notes: Dict[str, Dict] = {}   # rel_path → note_data
        self._idf: Dict[str, float] = {}
        self._built = False

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
            from obsidian import parse_frontmatter, safe_filename
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

        self._notes = notes
        self._idf = idf
        self._built = True

        self._save()
        if verbose:
            print(f"[indexer] 인덱싱 완료: {len(notes)}개 노트")
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

    # ── 검색 ─────────────────────────────────────────────────
    def search(self, query: str, limit: int = 10) -> List[Dict[str, Any]]:
        """쿼리 텍스트와 관련된 노트를 TF-IDF 점수로 정렬해 반환한다."""
        if not self._built:
            if not self.load():
                return []
        query_tokens = _tokenize(query)
        if not query_tokens:
            return []
        scores: Dict[str, float] = {}
        for token in set(query_tokens):
            idf_val = self._idf.get(token, 0.0)
            if idf_val == 0:
                continue
            for rel, note in self._notes.items():
                tf_val = note["tf"].get(token, 0.0)
                if tf_val > 0:
                    scores[rel] = scores.get(rel, 0.0) + tf_val * idf_val

        ranked = sorted(scores.items(), key=lambda x: -x[1])[:limit]
        results = []
        for rel, score in ranked:
            note = self._notes[rel]
            results.append({
                "path": rel,
                "title": note["title"],
                "wikilink_title": note["wikilink_title"],
                "snippet": note["snippet"],
                "score": round(score, 4),
                "date": note["date"],
                "type": note["type"],
            })
        return results

    def find_related(self, text: str, limit: int = 5,
                     min_score: float = 0.05) -> List[str]:
        """텍스트와 관련된 노트의 [[wiki link]] 타이틀 리스트를 반환한다.
        min_score 미만의 낮은 연관성 노트는 제외한다."""
        results = self.search(text, limit=limit)
        return [r["wikilink_title"] for r in results if r["score"] >= min_score]

    def get_note_content(self, rel_path: str) -> Optional[str]:
        """볼트에서 노트 내용을 직접 읽는다 (Q&A 컨텍스트용)."""
        full = os.path.join(self.vault_path, rel_path.replace("/", os.sep))
        if not os.path.exists(full):
            return None
        try:
            return open(full, encoding="utf-8", errors="replace").read()
        except Exception:
            return None

    # ── 팩토리 ────────────────────────────────────────────────
    @classmethod
    def from_config(cls) -> Optional["VaultIndexer"]:
        vault = _c("indexing.vault_path") or _c("obsidian.vault_path", "")
        index = _c("indexing.index_path", "data/vault_index.json")
        if not vault:
            try:
                from obsidian import _detect_obsidian_config
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
    ap.add_argument("--search", default="", help="검색 쿼리")
    ap.add_argument("--limit", type=int, default=10)
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    if not args.vault:
        print("오류: --vault 경로 필요 (또는 config.indexing.vault_path 설정)")
        return 1

    indexer = VaultIndexer(args.vault, args.index)

    if args.build:
        n = indexer.build(verbose=True)
        print(f"[indexer] 완료: {n}개 노트")

    if args.search:
        if not args.build:
            indexer.load()
        results = indexer.search(args.search, limit=args.limit)
        if not results:
            print("[indexer] 결과 없음")
        for r in results:
            print(f"  [{r['score']:.4f}] {r['title']}")
            if args.verbose:
                print(f"           {r['snippet'][:100]}")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main() or 0)
