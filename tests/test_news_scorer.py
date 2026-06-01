"""Tests for core.llm.news_scorer."""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from core.llm.news_scorer import NewsScore, score_batch, score_news


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_client(response_text: str) -> MagicMock:
    """Return a mock Anthropic client whose messages.create() returns response_text."""
    mock_content = SimpleNamespace(text=response_text)
    mock_msg = SimpleNamespace(content=[mock_content])
    mock_client = MagicMock()
    mock_client.messages.create.return_value = mock_msg
    return mock_client


_VALID_JSON = json.dumps(
    {"score": 0.7, "confidence": 0.85, "summary": "Strong earnings beat expectations"}
)

_BEARISH_JSON = json.dumps(
    {"score": -0.5, "confidence": 0.6, "summary": "Regulatory headwinds weigh on stock"}
)


# ---------------------------------------------------------------------------
# score_news — happy path
# ---------------------------------------------------------------------------

class TestScoreNewsHappyPath:
    def test_returns_news_score_object(self):
        client = _make_client(_VALID_JSON)
        result = score_news(
            symbol="RELIANCE",
            headlines=["Q4 profit beats estimates by 12%"],
            client=client,
        )
        assert isinstance(result, NewsScore)

    def test_symbol_preserved(self):
        client = _make_client(_VALID_JSON)
        result = score_news(
            symbol="TCS",
            headlines=["TCS wins $100M deal"],
            client=client,
        )
        assert result is not None
        assert result.symbol == "TCS"

    def test_score_value(self):
        client = _make_client(_VALID_JSON)
        result = score_news(
            symbol="INFY",
            headlines=["Infosys raises guidance"],
            client=client,
        )
        assert result is not None
        assert abs(result.score - 0.7) < 1e-6

    def test_confidence_value(self):
        client = _make_client(_VALID_JSON)
        result = score_news(
            symbol="HDFC",
            headlines=["HDFC merger complete"],
            client=client,
        )
        assert result is not None
        assert abs(result.confidence - 0.85) < 1e-6

    def test_summary_text(self):
        client = _make_client(_VALID_JSON)
        result = score_news(
            symbol="WIPRO",
            headlines=["Wipro announces buyback"],
            client=client,
        )
        assert result is not None
        assert result.summary == "Strong earnings beat expectations"

    def test_raw_response_stored(self):
        client = _make_client(_VALID_JSON)
        result = score_news(
            symbol="SBIN",
            headlines=["SBI beats NPA targets"],
            client=client,
        )
        assert result is not None
        assert result.raw_response == _VALID_JSON

    def test_bearish_score(self):
        client = _make_client(_BEARISH_JSON)
        result = score_news(
            symbol="ONGC",
            headlines=["Crude oil prices crash", "ONGC profit warning issued"],
            client=client,
        )
        assert result is not None
        assert result.score == pytest.approx(-0.5)

    def test_client_called_with_correct_model(self):
        client = _make_client(_VALID_JSON)
        score_news(
            symbol="MARUTI",
            headlines=["Maruti Q1 volumes up 8%"],
            model="claude-haiku-4-5-20251001",
            client=client,
        )
        call_kwargs = client.messages.create.call_args
        assert call_kwargs.kwargs["model"] == "claude-haiku-4-5-20251001"

    def test_multiple_headlines_joined_in_prompt(self):
        client = _make_client(_VALID_JSON)
        headlines = ["Headline one", "Headline two", "Headline three"]
        score_news(symbol="LT", headlines=headlines, client=client)
        prompt = client.messages.create.call_args.kwargs["messages"][0]["content"]
        for h in headlines:
            assert h in prompt

    def test_max_tokens_is_small(self):
        """Haiku call should request at most 256 tokens (cheap)."""
        client = _make_client(_VALID_JSON)
        score_news(symbol="BAJFINANCE", headlines=["BAJFINANCE up 3%"], client=client)
        assert client.messages.create.call_args.kwargs["max_tokens"] <= 256


# ---------------------------------------------------------------------------
# score_news — fail-open cases
# ---------------------------------------------------------------------------

class TestScoreNewsFailOpen:
    def test_returns_none_on_empty_headlines(self):
        client = _make_client(_VALID_JSON)
        result = score_news(symbol="RELIANCE", headlines=[], client=client)
        assert result is None

    def test_does_not_call_api_on_empty_headlines(self):
        client = _make_client(_VALID_JSON)
        score_news(symbol="RELIANCE", headlines=[], client=client)
        client.messages.create.assert_not_called()

    def test_returns_none_on_empty_api_key_without_client(self):
        result = score_news(
            symbol="TCS",
            headlines=["TCS wins deal"],
            api_key="",
            client=None,
        )
        assert result is None

    def test_returns_none_on_api_exception(self):
        client = MagicMock()
        client.messages.create.side_effect = RuntimeError("network error")
        result = score_news(
            symbol="INFY",
            headlines=["INFY wins contract"],
            client=client,
        )
        assert result is None

    def test_returns_none_on_invalid_json(self):
        client = _make_client("this is not json at all")
        result = score_news(
            symbol="WIPRO",
            headlines=["WIPRO layoffs"],
            client=client,
        )
        assert result is None

    def test_returns_none_on_missing_score_key(self):
        bad_json = json.dumps({"confidence": 0.5, "summary": "ok"})
        client = _make_client(bad_json)
        result = score_news(
            symbol="NTPC",
            headlines=["NTPC dividend announced"],
            client=client,
        )
        assert result is None

    def test_returns_none_on_empty_json_object(self):
        client = _make_client("{}")
        result = score_news(
            symbol="COALINDIA",
            headlines=["Coal India output up"],
            client=client,
        )
        assert result is None

    def test_handles_markdown_fenced_json(self):
        """Model sometimes wraps JSON in ```json ... ``` fences."""
        fenced = "```json\n" + _VALID_JSON + "\n```"
        client = _make_client(fenced)
        result = score_news(
            symbol="HDFCBANK",
            headlines=["HDFC Bank NIM improves"],
            client=client,
        )
        assert result is not None
        assert abs(result.score - 0.7) < 1e-6

    def test_score_clamped_above_1(self):
        """Model returns out-of-range value; should be clamped to 1.0."""
        clamped_json = json.dumps({"score": 1.5, "confidence": 0.9, "summary": "extreme"})
        client = _make_client(clamped_json)
        result = score_news(
            symbol="ADANIENT",
            headlines=["Adani wins mega port contract"],
            client=client,
        )
        assert result is not None
        assert result.score == pytest.approx(1.0)

    def test_score_clamped_below_minus_1(self):
        clamped_json = json.dumps({"score": -2.0, "confidence": 0.9, "summary": "crash"})
        client = _make_client(clamped_json)
        result = score_news(
            symbol="ADANIPORTS",
            headlines=["Adani short report published"],
            client=client,
        )
        assert result is not None
        assert result.score == pytest.approx(-1.0)

    def test_confidence_clamped_above_1(self):
        clamped_json = json.dumps({"score": 0.3, "confidence": 1.5, "summary": "ok"})
        client = _make_client(clamped_json)
        result = score_news(symbol="TATAPOWER", headlines=["Tata Power wins solar bid"], client=client)
        assert result is not None
        assert result.confidence == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# score_batch
# ---------------------------------------------------------------------------

class TestScoreBatch:
    def test_returns_dict_of_news_scores(self):
        client = _make_client(_VALID_JSON)
        results = score_batch(
            {"RELIANCE": ["Q4 profit beats"], "TCS": ["TCS wins deal"]},
            client=client,
        )
        assert set(results.keys()) == {"RELIANCE", "TCS"}
        assert all(isinstance(v, NewsScore) for v in results.values())

    def test_omits_symbols_with_empty_headlines(self):
        client = _make_client(_VALID_JSON)
        results = score_batch(
            {"RELIANCE": ["Good news"], "EMPTY_CO": []},
            client=client,
        )
        assert "RELIANCE" in results
        assert "EMPTY_CO" not in results

    def test_returns_empty_dict_when_no_api_key_or_client(self):
        results = score_batch(
            {"RELIANCE": ["headline1"]},
            api_key="",
            client=None,
        )
        assert results == {}

    def test_calls_api_once_per_symbol(self):
        client = _make_client(_VALID_JSON)
        score_batch(
            {"A": ["h1"], "B": ["h2"], "C": ["h3"]},
            client=client,
        )
        assert client.messages.create.call_count == 3

    def test_partial_failure_omitted(self):
        """If one symbol's API call fails, the others should still succeed."""
        good_json = _VALID_JSON
        call_count = 0

        def side_effect(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                raise RuntimeError("transient error")
            mock_content = SimpleNamespace(text=good_json)
            return SimpleNamespace(content=[mock_content])

        client = MagicMock()
        client.messages.create.side_effect = side_effect

        results = score_batch(
            {"A": ["h1"], "B": ["h2"], "C": ["h3"]},
            client=client,
        )
        # A and C succeed, B fails
        assert "A" in results
        assert "B" not in results
        assert "C" in results

    def test_empty_input_returns_empty_dict(self):
        client = _make_client(_VALID_JSON)
        results = score_batch({}, client=client)
        assert results == {}
