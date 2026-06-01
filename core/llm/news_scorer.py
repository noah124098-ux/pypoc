"""Claude-powered news sentiment scorer for Nifty 50 stocks.

Usage::

    from core.llm.news_scorer import score_news, score_batch, NewsScore

    result = score_news(
        symbol="RELIANCE",
        headlines=["Reliance Q4 profit beats estimates", "New JioMart expansion plan"],
        model="claude-haiku-4-5-20251001",
        api_key=secrets.anthropic_api_key,
    )
    if result:
        print(result.score, result.summary)
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Optional

logger = logging.getLogger(__name__)

_PROMPT_TEMPLATE = """\
You are a financial sentiment analyst helping a short-term intraday trader.

Given these recent news headlines for {symbol}:
{headlines_block}

Rate the overall sentiment for a short-term (1-5 day) intraday trader on a scale from \
-1.0 (very bearish) to +1.0 (very bullish).

Respond with valid JSON only — no explanation, no markdown, no code fences:
{{"score": <float between -1.0 and 1.0>, "confidence": <float between 0.0 and 1.0>, \
"summary": "<one-line explanation>"}}"""


# ---------------------------------------------------------------------------
# Public data classes
# ---------------------------------------------------------------------------

@dataclass
class NewsScore:
    symbol: str
    score: float        # -1.0 (very bearish) to +1.0 (very bullish)
    confidence: float   # 0.0-1.0
    summary: str        # one-line explanation
    raw_response: str


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _build_prompt(symbol: str, headlines: list[str]) -> str:
    headlines_block = "\n".join(f"- {h}" for h in headlines)
    return _PROMPT_TEMPLATE.format(symbol=symbol, headlines_block=headlines_block)


def _parse_response(raw: str) -> tuple[float, float, str]:
    """Parse JSON response from Claude. Returns (score, confidence, summary)."""
    text = raw.strip()

    # Strip markdown code fences if the model wraps its JSON anyway
    if text.startswith("```"):
        lines = text.splitlines()
        inner: list[str] = []
        in_block = False
        for line in lines:
            if line.startswith("```") and not in_block:
                in_block = True
                continue
            if line.startswith("```") and in_block:
                break
            if in_block:
                inner.append(line)
        text = "\n".join(inner)

    data = json.loads(text)

    score = float(data["score"])
    confidence = float(data["confidence"])
    summary = str(data.get("summary", ""))

    # Clamp to valid ranges
    score = max(-1.0, min(1.0, score))
    confidence = max(0.0, min(1.0, confidence))

    return score, confidence, summary


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def score_news(
    symbol: str,
    headlines: list[str],
    model: str = "claude-haiku-4-5-20251001",
    api_key: str = "",
    client: Any = None,   # injectable for tests
) -> Optional[NewsScore]:
    """Score news sentiment for a single symbol.

    Parameters
    ----------
    symbol:
        NSE ticker symbol (e.g. ``"RELIANCE"``).
    headlines:
        List of recent news headlines for this stock.
    model:
        Anthropic model ID. Defaults to claude-haiku (cheap and fast).
    api_key:
        Anthropic API key. If empty and no *client* is provided, returns ``None``.
    client:
        Optional pre-constructed ``anthropic.Anthropic`` client (useful for testing).

    Returns
    -------
    :class:`NewsScore` on success, ``None`` if inputs are missing, the API key is
    absent, or the call/parse fails for any reason (fail-open).
    """
    if not headlines:
        logger.debug("score_news: no headlines for %s, skipping.", symbol)
        return None

    if not api_key and client is None:
        logger.debug("score_news: no API key and no injected client, skipping.")
        return None

    prompt = _build_prompt(symbol, headlines)

    try:
        if client is None:
            import anthropic as _anthropic
            client = _anthropic.Anthropic(api_key=api_key)

        msg = client.messages.create(
            model=model,
            max_tokens=256,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = msg.content[0].text

    except Exception as exc:
        logger.warning("score_news API call failed for %s: %s", symbol, exc)
        return None

    try:
        score, confidence, summary = _parse_response(raw)
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        logger.warning(
            "score_news response parse failed for %s: %s. Raw: %.200s",
            symbol,
            exc,
            raw,
        )
        return None

    return NewsScore(
        symbol=symbol,
        score=score,
        confidence=confidence,
        summary=summary,
        raw_response=raw,
    )


def score_batch(
    symbols_with_headlines: dict[str, list[str]],
    model: str = "claude-haiku-4-5-20251001",
    api_key: str = "",
    client: Any = None,   # injectable for tests
) -> dict[str, NewsScore]:
    """Score news sentiment for multiple symbols.

    Calls :func:`score_news` for each symbol in sequence (haiku is fast and cheap).
    Symbols that return ``None`` are omitted from the result dict.

    Parameters
    ----------
    symbols_with_headlines:
        Mapping of ``symbol -> list[headline]``.
    model:
        Anthropic model ID passed through to each :func:`score_news` call.
    api_key:
        Anthropic API key.
    client:
        Optional injectable client (shared across all calls in the batch).

    Returns
    -------
    Dict of symbol -> :class:`NewsScore` for every symbol that scored successfully.
    """
    results: dict[str, NewsScore] = {}
    for symbol, headlines in symbols_with_headlines.items():
        result = score_news(
            symbol=symbol,
            headlines=headlines,
            model=model,
            api_key=api_key,
            client=client,
        )
        if result is not None:
            results[symbol] = result
    return results
