# -*- coding: utf-8 -*-
"""_semantic_cut — 의미 검색 노이즈 컷의 회귀 테스트.

이 컷의 상수는 실측으로 정했다(vault_indexer._SEMANTIC_MIN_Z 주석 참고). 여기서는
**성질**을 고정한다: 절대 코사인 문턱으로 되돌아가지 않을 것, 표본이 적을 때 억지로
통계를 쓰지 않을 것, 그리고 "코사인 값이 붙었다 = 컷을 통과했다"는 계약이 유지될 것.
"""

import pytest

from meeting_minutes_app.wiki_core import vault_indexer as vi


def _sims(vals):
    """(rel, cos) 내림차순 목록으로 변환."""
    ordered = sorted(vals, reverse=True)
    return [(f"n{i}.md", v) for i, v in enumerate(ordered)]


@pytest.fixture
def z_cut(monkeypatch):
    """config 를 z=1.5 로 고정(실제 config.json 에 의존하지 않게)."""
    monkeypatch.setattr(vi, "_c", lambda key, default=None: {
        "wiki_knowledge.embedding_min_z": 1.5,
    }.get(key, default))


class TestSmallSampleGuard:
    def test_below_min_samples_returns_top_n_unchanged(self, z_cut):
        """후보가 적으면 표준편차가 불안정하다 — 컷하지 않고 상위 N 을 준다."""
        sims = _sims([0.9, 0.8, 0.7, 0.6, 0.5])
        assert vi._semantic_cut(sims, 3) == sims[:3]

    def test_empty(self, z_cut):
        assert vi._semantic_cut([], 5) == []


class TestZCut:
    def test_flat_distribution_keeps_nothing_special(self, z_cut):
        """전부 비슷한 점수 = 가를 근거가 없다 — 아무것도 통과하지 못한다.

        (평균 근처에 몰려 있으면 z 가 1.5 에 못 미친다. '관련 노트 없음'이
        무관한 노트를 근거로 올리는 것보다 정직하다.)"""
        sims = _sims([0.50 + (i % 3) * 0.001 for i in range(60)])
        assert vi._semantic_cut(sims, 10) == []

    def test_clear_outlier_survives(self, z_cut):
        """분포에서 확실히 튀는 1건만 통과한다."""
        vals = [0.30 + (i * 0.0005) for i in range(59)] + [0.95]
        keep = vi._semantic_cut(_sims(vals), 10)
        assert len(keep) == 1
        assert keep[0][1] == pytest.approx(0.95)

    def test_identical_scores_do_not_crash(self, z_cut):
        """표준편차 0 — 나눗셈 폭발 없이 상위 N 을 준다."""
        sims = _sims([0.5] * 60)
        assert len(vi._semantic_cut(sims, 4)) == 4

    def test_respects_limit(self, z_cut):
        """z 를 통과해도 limit 를 넘지 않는다."""
        vals = [0.10] * 50 + [0.99] * 10
        keep = vi._semantic_cut(_sims(vals), 3)
        assert len(keep) == 3

    def test_cut_is_relative_not_absolute(self, z_cut):
        """같은 코사인 값이 분포에 따라 통과·탈락으로 갈려야 한다.

        절대 문턱으로 되돌아가면 이 테스트가 깨진다. 실측에서 절대 문턱은 성립하지
        않았다 — 진짜 양성의 평균(0.576)이 무관 분포의 p95(0.619)보다 낮았다."""
        # 0.62 가 낮은 분포에서는 통과
        low = [0.20 + i * 0.0005 for i in range(59)] + [0.62]
        assert any(s == pytest.approx(0.62)
                   for _, s in vi._semantic_cut(_sims(low), 10))
        # 같은 0.62 가 높은 분포에서는 탈락
        high = [0.60 + i * 0.0005 for i in range(59)] + [0.62]
        assert not any(s == pytest.approx(0.62)
                       for _, s in vi._semantic_cut(_sims(high), 10))


class TestDisableSwitch:
    def test_zero_disables_cut(self, monkeypatch):
        monkeypatch.setattr(vi, "_c", lambda key, default=None: {
            "wiki_knowledge.embedding_min_z": 0,
        }.get(key, default))
        sims = _sims([0.5] * 60)
        assert len(vi._semantic_cut(sims, 7)) == 7

    def test_bad_value_falls_back_to_default(self, monkeypatch):
        """설정에 문자열이 들어와도 죽지 않고 기본 문턱을 쓴다."""
        monkeypatch.setattr(vi, "_c", lambda key, default=None: {
            "wiki_knowledge.embedding_min_z": "아무거나",
        }.get(key, default))
        vals = [0.30 + i * 0.0005 for i in range(59)] + [0.95]
        keep = vi._semantic_cut(_sims(vals), 10)
        assert len(keep) == 1


class TestRetiredConstant:
    def test_absolute_cosine_floor_is_gone(self):
        """구 상수(embedding_min_cosine)를 다시 읽지 않는다 — 규칙이 두 곳으로
        갈리면 실측으로 반박한 동작이 되살아난다."""
        import inspect
        src = inspect.getsource(vi)
        # 주석·docstring 의 설명 언급은 허용하되, 설정을 실제로 읽는 코드는 없어야 한다
        assert 'embedding_min_cosine"' not in src.replace("`embedding_min_cosine`", "")

    def test_wiki_ask_does_not_reread_cosine_floor(self):
        import inspect
        from meeting_minutes_app.wiki_core import wiki_ask
        src = inspect.getsource(wiki_ask)
        assert 'embedding_min_cosine"' not in src.replace("`embedding_min_cosine`", "")
