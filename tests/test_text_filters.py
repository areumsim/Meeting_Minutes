"""STT 환각·반복 정화 필터 회귀 테스트 (common/text_filters.py).

방지하려는 재발 버그 (2026-07-28, 한국어 회의 실시간 전사에서 실제 관측):
  1) 무음 구간 환각으로 러시아어/정체불명 라틴 조각이 섞임
     ("Na velolodu", "где-нибудь", "Okei")
  2) prompt 되먹임 루프로 같은 문장이 수십 번 반복
  3) 과거 _CJK_RANGES 의 마지막 범위가 U+8C48~U+FAFF 로 잘못 적히면 한글(U+AC00~)이
     통째로 CJK 환각 판정돼 한국어 전사가 전부 사라진다 — 문자 범위 회귀 테스트 포함

실행:
    python -m pytest tests/test_text_filters.py -q
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from meeting_minutes_app.common import text_filters as tf  # noqa: E402


# ━━━━━━━━ 문자 범위 (오탐 방지의 토대) ━━━━━━━━

class TestScriptRanges:
    def test_hangul_is_not_cjk(self):
        # 한글이 CJK 범위에 삼켜지면 한국어 전사 전체가 환각으로 삭제된다
        for s in ["뭐가 있냐.", "회의록 자동화 검토", "가", "힣", "네 알겠습니다"]:
            assert not tf.is_cjk_hallucination(s), s

    def test_chinese_japanese_detected(self):
        assert tf.is_cjk_hallucination("中文测试 中文测试")
        assert tf.is_cjk_hallucination("これはテストです")

    def test_korean_and_english_mix_is_clean(self):
        for s in ["AI 프로젝트 Claude Code 도입 검토",
                  "OneDrive 라이선스 비용이 연 1200만원입니다.",
                  "This is a normal English sentence."]:
            assert not tf.is_script_mismatch(s, "ko"), s
            assert tf.foreign_script_ratio(s) == 0.0

    def test_cyrillic_flagged(self):
        assert tf.is_script_mismatch("где-нибудь 뭐가 있냐.", "ko")
        assert tf.is_script_mismatch("Привет", "ko")


# ━━━━━━━━ 반복 축약 ━━━━━━━━

class TestCollapseRepetitions:
    def test_token_loop(self):
        assert tf.collapse_repetitions("뭐가 있냐 뭐가 있냐 뭐가 있냐") == "뭐가 있냐"

    def test_sentence_loop(self):
        assert tf.collapse_repetitions("A입니다. B입니다. A입니다. B입니다.") == "A입니다. B입니다."

    def test_unaligned_phrase_loop(self):
        # 반복 시작 위치가 n 배수에 맞지 않아도 축약돼야 한다(과거 정렬 버그)
        text = ("제가 가이드를 줬거든요 원드라이브를 쓰면 이 돈을 안 써도 된다 "
                "원드라이브를 쓰면 이 돈을 안 써도 된다 "
                "원드라이브를 쓰면 이 돈을 안 써도 된다")
        assert tf.collapse_repetitions(text) == \
            "제가 가이드를 줬거든요 원드라이브를 쓰면 이 돈을 안 써도 된다"

    def test_normal_text_untouched(self):
        for s in ["정상적인 한국어 문장입니다. 다른 내용입니다.",
                  "이번 주 안에 릴리즈 하려고 하니까 제일 중요한 거죠.",
                  ""]:
            assert tf.collapse_repetitions(s) == s

    def test_unique_ratio(self):
        assert tf.unique_ratio(["a", "b", "c"]) == 1.0
        assert tf.unique_ratio(["같은 문장.", "같은 문장.", "같은 문장.", "같은 문장."]) == 0.25


# ━━━━━━━━ 세그먼트 중복 제거 ━━━━━━━━

class TestDedupeSegments:
    def _segs(self, texts):
        return [{"text": t, "start": float(i), "end": i + 1.0}
                for i, t in enumerate(texts)]

    def test_repeated_long_sentence_collapsed(self):
        segs = self._segs(["그 파일을 다 일반으로 바꿔서 올려주시면 돼요."] * 8)
        kept, dropped = tf.dedupe_segments(segs)
        assert len(kept) == 1 and dropped == 7

    def test_short_backchannel_preserved(self):
        segs = self._segs(["네.", "네.", "맞아요.", "네.", "맞아요."])
        kept, dropped = tf.dedupe_segments(segs)
        assert dropped == 0 and len(kept) == 5

    def test_distinct_content_preserved(self):
        segs = self._segs(["첫 번째 안건입니다.", "두 번째 안건입니다.", "세 번째 안건입니다."])
        kept, dropped = tf.dedupe_segments(segs)
        assert dropped == 0 and len(kept) == 3

    def test_near_duplicate_detected(self):
        assert tf.is_near_duplicate("그 파일을 다 일반으로 바꿔서 올려주시면 돼요.",
                                    "그 파일을 다 일반으로 바꿔서 올려주시면 돼요")
        assert not tf.is_near_duplicate("첫 번째 안건입니다.", "예산 승인 건입니다.")


# ━━━━━━━━ 전사 정화 (공용 진입점) ━━━━━━━━

class TestSanitizeTranscript:
    #: 사용자가 보고한 실제 전사 일부(무음 환각 + 반복)
    REAL = [
        "Na velolodu.",
        "где-нибудь 뭐가 있냐.",
        "뭐라고 했나 봐요.",
        "Okei.",
        "뭐가 있냐.",
        "Na velolodu.",
        "뭐가 있냐.",
        "Na velolodu.",
        "간식 사고 밥 먹고 이것밖에 안 해봐가지고.",
        "간식 사고 밥 먹고 이것밖에 안 해봐가지고.",
        "간식 사고 밥 먹고 이것밖에 안 해봐가지고.",
        "이번 주 안에 릴리즈 하려고 하니까 제일 중요한 거죠.",
    ]

    def _run(self, texts, language="ko"):
        segs = [{"text": t, "start": float(i), "end": i + 1.0, "text_original": t}
                for i, t in enumerate(texts)]
        return tf.sanitize_transcript(segs, language)

    def test_real_transcript_cleaned(self):
        out, stats = self._run(self.REAL)
        texts = [s["text"] for s in out]
        # 반복 제거
        assert texts.count("간식 사고 밥 먹고 이것밖에 안 해봐가지고.") == 1
        assert sum(1 for t in texts if "Na velolodu" in t) == 1
        # 반복되는 정체불명 라틴 조각 + 키릴은 표시(삭제 아님)
        assert any(t.startswith(tf.SUSPECT_MARKER) and "Na velolodu" in t for t in texts)
        assert any(t.startswith(tf.SUSPECT_MARKER) and "где-нибудь" in t for t in texts)
        # 정상 한국어 발화는 손대지 않는다
        assert "이번 주 안에 릴리즈 하려고 하니까 제일 중요한 거죠." in texts
        assert "뭐라고 했나 봐요." in texts
        assert stats["deduped"] > 0 and stats["marked"] > 0

    def test_single_english_aside_not_marked(self):
        # 1회 등장하는 영어 한마디는 정상 — 표시하지 않는다
        out, stats = self._run(["Okay.", "네 진행하겠습니다.", "Claude Code 도입 검토합니다."])
        assert stats["marked"] == 0
        assert all(not s["text"].startswith(tf.SUSPECT_MARKER) for s in out)

    def test_intra_segment_loop_collapsed(self):
        out, stats = self._run(["제가 가이드를 줬거든요 제가 가이드를 줬거든요 제가 가이드를 줬거든요"])
        assert out[0]["text"] == "제가 가이드를 줬거든요"
        assert out[0]["text_original"] == "제가 가이드를 줬거든요"
        assert stats["collapsed"] == 1

    def test_empty_segments_removed(self):
        out, stats = self._run(["", "   ", "실제 내용입니다."])
        assert len(out) == 1 and stats["empty"] == 2

    def test_disabled_is_passthrough(self):
        segs = [{"text": t} for t in ["같은 말.", "같은 말.", "같은 말."]]
        out, stats = tf.sanitize_transcript(segs, "ko", enabled=False)
        assert out == segs and not any(stats.values())

    def test_idempotent(self):
        once, _ = self._run(self.REAL)
        twice, stats2 = tf.sanitize_transcript(once, "ko")
        assert [s["text"] for s in twice] == [s["text"] for s in once]
        assert stats2["marked"] == 0 or all(
            s["text"].count(tf.SUSPECT_MARKER) == 1 for s in twice)

    def test_language_auto_infers(self):
        out, _ = self._run(["한국어 회의 내용입니다."], language="auto")
        assert out[0]["text"] == "한국어 회의 내용입니다."
        assert tf.infer_language([{"text": "한국어 문장"}]) == "ko"
        assert tf.infer_language([{"text": "english only sentence"}]) == "en"

    def test_stats_line(self):
        assert tf.sanitize_stats_line({}) == ""
        assert tf.sanitize_stats_line({"deduped": 3, "marked": 1, "collapsed": 0,
                                       "empty": 0}) == "중복제거 3, 환각표시 1"
