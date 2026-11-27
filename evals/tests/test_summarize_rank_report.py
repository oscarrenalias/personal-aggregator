"""Tests for evals/summarize_rank/report.py metrics on synthetic data."""

import csv
import json
import math
from pathlib import Path

# conftest.py adds evals/ to sys.path; use package import to avoid name
# collision with evals/clusterer_merge/report.py.
from summarize_rank import report as sr_report


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_result(article_id: int, prompt_label: str, score: int, entities: list | None = None, production_score: int = 50) -> dict:
    """Build a synthetic promptfoo result dict."""
    parsed_output = {
        "summary": f"Summary for article {article_id}",
        "topics": ["topic1"],
        "importance_score": score,
        "importance_reason": "test",
        "entities": entities or [],
        "categories": [],
    }
    return {
        "prompt": {"label": prompt_label},
        "provider": {"label": "luna-low"},
        "testCase": {
            "vars": {
                "article_id": article_id,
                "title": f"Article {article_id} title",
                "production_score": production_score,
            },
            "metadata": {
                "article_id": article_id,
                "production_score": production_score,
            },
        },
        "response": {"output": json.dumps(parsed_output)},
    }


def _make_parse_error_result(article_id: int, prompt_label: str, production_score: int = 50) -> dict:
    return {
        "prompt": {"label": prompt_label},
        "provider": {"label": "luna-low"},
        "testCase": {
            "vars": {"article_id": article_id, "title": f"Article {article_id}", "production_score": production_score},
            "metadata": {"article_id": article_id, "production_score": production_score},
        },
        "response": {"output": "not valid json"},
    }


# ---------------------------------------------------------------------------
# collect_paired_scores
# ---------------------------------------------------------------------------

class TestCollectPairedScores:
    def test_basic_pair(self):
        results = [
            _make_result(1, "v2", 70, production_score=72),
            _make_result(1, "v3", 73, entities=["OpenAI", "GPT"], production_score=72),
        ]
        per_article, parse_errors, totals = sr_report._collect_paired_scores(results)

        assert 1 in per_article
        assert per_article[1]["v2_score"] == 70
        assert per_article[1]["v3_score"] == 73
        assert per_article[1]["v3_entities"] == ["OpenAI", "GPT"]
        assert parse_errors == {"v2": 0, "v3": 0}
        assert totals == {"v2": 1, "v3": 1}

    def test_parse_error_counted(self):
        results = [
            _make_result(1, "v2", 70),
            _make_parse_error_result(1, "v3"),
        ]
        per_article, parse_errors, _ = sr_report._collect_paired_scores(results)

        assert parse_errors["v3"] == 1
        assert 1 not in per_article or "v3_score" not in per_article.get(1, {})


# ---------------------------------------------------------------------------
# Spearman rho
# ---------------------------------------------------------------------------

class TestSpearmanRho:
    def test_perfect_correlation(self):
        x = [1.0, 2.0, 3.0, 4.0, 5.0]
        y = [1.0, 2.0, 3.0, 4.0, 5.0]
        rho = sr_report._spearman_rho(x, y)
        assert abs(rho - 1.0) < 1e-6

    def test_perfect_anticorrelation(self):
        x = [1.0, 2.0, 3.0, 4.0, 5.0]
        y = [5.0, 4.0, 3.0, 2.0, 1.0]
        rho = sr_report._spearman_rho(x, y)
        assert abs(rho - (-1.0)) < 1e-6

    def test_single_element_nan(self):
        rho = sr_report._spearman_rho([1.0], [1.0])
        assert math.isnan(rho)

    def test_ties_handled(self):
        x = [1.0, 1.0, 2.0]
        y = [1.0, 1.0, 2.0]
        rho = sr_report._spearman_rho(x, y)
        assert abs(rho - 1.0) < 0.01


# ---------------------------------------------------------------------------
# _analyze metrics
# ---------------------------------------------------------------------------

class TestAnalyzeMetrics:
    def _build_per_article(self, pairs: list[tuple[int, int, int, int | None, list | None]]) -> dict:
        """pairs: (article_id, v2_score, v3_score, production_score, entities)"""
        per_article = {}
        for aid, v2, v3, prod, ents in pairs:
            per_article[aid] = {
                "v2_score": v2,
                "v3_score": v3,
                "production_score": prod,
                "v3_entities": ents or [],
                "title": f"Article {aid}",
            }
        return per_article

    def test_mae_calculation(self):
        # v2=[70,80], v3=[72,82] → diffs=[2,2] → mae=2
        per_article = self._build_per_article([
            (1, 70, 72, 70, ["E1"]),
            (2, 80, 82, 80, ["E2"]),
        ])
        metrics = sr_report._analyze(per_article, {"v2": 0, "v3": 0}, {"v2": 2, "v3": 2}, 66)
        assert abs(metrics["mae_v3_v2"] - 2.0) < 1e-6

    def test_flip_rate_above_threshold(self):
        # article 1: v2=70 (>=66), v3=60 (<66) → flip
        # article 2: v2=50 (<66), v3=50 (<66) → no flip
        per_article = self._build_per_article([
            (1, 70, 60, 70, ["E1"]),
            (2, 50, 50, 50, []),
        ])
        metrics = sr_report._analyze(per_article, {"v2": 0, "v3": 0}, {"v2": 2, "v3": 2}, 66)
        assert metrics["flips_total"] == 1
        assert abs(metrics["flip_rate"] - 0.5) < 1e-6

    def test_entity_coverage(self):
        # 3 articles, 2 have entities → 66.7% coverage
        per_article = self._build_per_article([
            (1, 70, 72, 70, ["E1", "E2"]),
            (2, 60, 61, 60, ["E3"]),
            (3, 50, 51, 50, []),
        ])
        metrics = sr_report._analyze(per_article, {"v2": 0, "v3": 0}, {"v2": 3, "v3": 3}, 66)
        assert abs(metrics["entity_coverage"] - 2 / 3) < 1e-6

    def test_v2_prod_noise_floor(self):
        # v2=70, prod=72 → mae=2
        per_article = self._build_per_article([
            (1, 70, 70, 72, ["E1"]),
        ])
        metrics = sr_report._analyze(per_article, {"v2": 0, "v3": 0}, {"v2": 1, "v3": 1}, 66)
        assert abs(metrics["mae_v2_prod"] - 2.0) < 1e-6


# ---------------------------------------------------------------------------
# _pass_fail criteria
# ---------------------------------------------------------------------------

class TestPassFail:
    def _passing_metrics(self) -> dict:
        return {
            "v2_parse_fail_rate": 0.0,
            "v3_parse_fail_rate": 0.0,
            "mae_v3_v2": 2.0,
            "flip_rate": 0.02,
            "rho": 0.95,
            "entity_coverage": 0.90,
            "median_entities": 3.0,
        }

    def test_all_pass(self):
        pf = sr_report._pass_fail(self._passing_metrics())
        assert all(pf.values()), f"Unexpected failures: {[k for k, v in pf.items() if not v]}"

    def test_parse_fail_too_high(self):
        m = self._passing_metrics()
        m["v3_parse_fail_rate"] = 0.01  # > 0.5%
        pf = sr_report._pass_fail(m)
        assert not pf["parse_fail_v3"]

    def test_mae_too_high(self):
        m = self._passing_metrics()
        m["mae_v3_v2"] = 5.0  # > 4
        pf = sr_report._pass_fail(m)
        assert not pf["mae"]

    def test_flip_rate_too_high(self):
        m = self._passing_metrics()
        m["flip_rate"] = 0.07  # > 5%
        pf = sr_report._pass_fail(m)
        assert not pf["flip_rate"]

    def test_rho_too_low(self):
        m = self._passing_metrics()
        m["rho"] = 0.85  # < 0.90
        pf = sr_report._pass_fail(m)
        assert not pf["rho"]

    def test_entity_coverage_too_low(self):
        m = self._passing_metrics()
        m["entity_coverage"] = 0.80  # < 85%
        pf = sr_report._pass_fail(m)
        assert not pf["entity_coverage"]

    def test_median_entities_out_of_range(self):
        m = self._passing_metrics()
        m["median_entities"] = 1.0  # < 2
        pf = sr_report._pass_fail(m)
        assert not pf["median_entities"]

        m2 = self._passing_metrics()
        m2["median_entities"] = 7.0  # > 6
        pf2 = sr_report._pass_fail(m2)
        assert not pf2["median_entities"]


# ---------------------------------------------------------------------------
# entity sheet
# ---------------------------------------------------------------------------

class TestEntitySheet:
    def test_entity_sheet_written(self, tmp_path: Path):
        per_article = {
            1: {"title": "Article One", "v3_entities": ["OpenAI", "GPT-5"], "v2_score": 70, "v3_score": 72},
            2: {"title": "Article Two", "v3_entities": ["Apple"], "v2_score": 60, "v3_score": 61},
            3: {"title": "Article Three", "v3_entities": [], "v2_score": 50, "v3_score": 51},
        }
        sheet_path = str(tmp_path / "sheet.csv")
        sr_report._write_entity_sheet(per_article, sheet_path, sample_size=10, seed=42)

        rows = []
        with open(sheet_path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            rows = list(reader)

        assert len(rows) == 3
        fieldnames = rows[0].keys() if rows else []
        assert "article_id" in fieldnames
        assert "title" in fieldnames
        assert "entities" in fieldnames
        assert "ok" in fieldnames
        assert "notes" in fieldnames

    def test_entity_sheet_respects_sample_size(self, tmp_path: Path):
        per_article = {
            i: {"title": f"Article {i}", "v3_entities": [f"Entity{i}"], "v2_score": 50, "v3_score": 51}
            for i in range(20)
        }
        sheet_path = str(tmp_path / "sheet.csv")
        sr_report._write_entity_sheet(per_article, sheet_path, sample_size=5, seed=0)

        with open(sheet_path, newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))

        assert len(rows) == 5

    def test_entity_sheet_deterministic_with_seed(self, tmp_path: Path):
        per_article = {
            i: {"title": f"Article {i}", "v3_entities": [f"E{i}"], "v2_score": 50, "v3_score": 51}
            for i in range(20)
        }
        path1 = str(tmp_path / "sheet1.csv")
        path2 = str(tmp_path / "sheet2.csv")
        sr_report._write_entity_sheet(per_article, path1, sample_size=5, seed=42)
        sr_report._write_entity_sheet(per_article, path2, sample_size=5, seed=42)

        content1 = Path(path1).read_text()
        content2 = Path(path2).read_text()
        assert content1 == content2


# ---------------------------------------------------------------------------
# JSON output mode
# ---------------------------------------------------------------------------

class TestJsonOutput:
    def test_json_output_via_file(self, tmp_path: Path):
        # Build a minimal results JSON file
        results_data = {
            "results": [
                _make_result(1, "v2", 70, production_score=72),
                _make_result(1, "v3", 72, entities=["E1"], production_score=72),
            ]
        }
        results_file = tmp_path / "results.json"
        results_file.write_text(json.dumps(results_data))

        import io
        import contextlib

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            try:
                sr_report.main([str(results_file), "--json"])
            except SystemExit:
                pass

        output = buf.getvalue()
        parsed = json.loads(output)
        assert "metrics" in parsed
        assert "pass_fail" in parsed
        assert "overall" in parsed
