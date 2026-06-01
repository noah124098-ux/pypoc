"""Tests for Angel One live portfolio fetcher.

All tests stub SmartConnect so they run offline (no network, no real credentials).
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch, call

import pytest

from core.data.angelone_portfolio import (
    fetch_live_portfolio,
    AccountSummary,
    LivePosition,
    _neutralize_order_methods,
    _refuse_order_call,
    _ORDER_METHODS,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_mock_smart_api(*, login_ok=True, positions=None, rms=None):
    """Return a MagicMock that mimics a SmartConnect session."""
    mock = MagicMock()
    mock.generateSession.return_value = (
        {"status": True, "data": {"jwtToken": "tok"}}
        if login_ok
        else {"status": False, "message": "bad credentials"}
    )

    mock.getPosition.return_value = {
        "status": True,
        "data": positions if positions is not None else [],
    }

    mock.getRMS.return_value = {
        "status": True,
        "data": rms if rms is not None else {
            "net": "100000",
            "availablecash": "80000",
            "utilisedamount": "20000",
        },
    }
    return mock


def _patch_smart_connect(mock_api, monkeypatch):
    """Patch SmartConnect constructor and pyotp where the module imports them."""
    monkeypatch.setattr(
        "SmartApi.SmartConnect",
        MagicMock(return_value=mock_api),
        raising=False,
    )
    monkeypatch.setattr(
        "pyotp.TOTP",
        lambda secret: MagicMock(now=lambda: "123456"),
        raising=False,
    )


CREDS = dict(api_key="k", client_code="c", password="p", totp_secret="JBSWY3DPEHPK3PXP")


# ---------------------------------------------------------------------------
# DATA-ONLY guard tests
# ---------------------------------------------------------------------------

class TestDataOnlyGuard:
    """Order methods on SmartConnect must be neutralized after instantiation."""

    def test_neutralize_replaces_all_order_methods(self):
        mock_api = MagicMock()
        for name in _ORDER_METHODS:
            setattr(mock_api, name, MagicMock(return_value={"status": True}))

        _neutralize_order_methods(mock_api)

        for name in _ORDER_METHODS:
            with pytest.raises(RuntimeError, match="DATA-ONLY"):
                getattr(mock_api, name)()

    def test_refuse_order_call_raises_runtime_error(self):
        with pytest.raises(RuntimeError, match="DATA-ONLY"):
            _refuse_order_call("anything")

    def test_order_methods_neutralized_before_session(self, monkeypatch):
        """Neutralization happens before generateSession, so even a failed login
        cannot leave live order methods active."""
        mock_api = _make_mock_smart_api(login_ok=False)
        mock_api.placeOrder = MagicMock(return_value={"status": True})
        _patch_smart_connect(mock_api, monkeypatch)

        fetch_live_portfolio(**CREDS)  # login fails -> returns None

        # placeOrder must now raise regardless of login outcome
        with pytest.raises(RuntimeError, match="DATA-ONLY"):
            mock_api.placeOrder({"symbol": "RELIANCE", "qty": 1})

    def test_order_methods_neutralized_after_successful_login(self, monkeypatch):
        mock_api = _make_mock_smart_api()
        mock_api.placeOrder = MagicMock(return_value={"status": True})
        mock_api.modifyOrder = MagicMock(return_value={"status": True})
        mock_api.cancelOrder = MagicMock(return_value={"status": True})
        mock_api.placeOrderFullResponse = MagicMock(return_value={"status": True})
        _patch_smart_connect(mock_api, monkeypatch)

        result = fetch_live_portfolio(**CREDS)
        assert result is not None  # login succeeded

        for name in _ORDER_METHODS:
            with pytest.raises(RuntimeError, match="DATA-ONLY"):
                getattr(mock_api, name)()


# ---------------------------------------------------------------------------
# Failure / None-return tests
# ---------------------------------------------------------------------------

class TestFailureHandling:

    def test_returns_none_when_login_fails(self, monkeypatch):
        mock_api = _make_mock_smart_api(login_ok=False)
        _patch_smart_connect(mock_api, monkeypatch)

        result = fetch_live_portfolio(**CREDS)
        assert result is None

    def test_returns_none_when_smartapi_import_raises(self, monkeypatch):
        """If SmartApi is not installed, fetch_live_portfolio returns None."""
        monkeypatch.setitem(
            __import__("sys").modules,
            "SmartApi",
            None,  # makes `import SmartApi` raise ImportError
        )
        result = fetch_live_portfolio(**CREDS)
        assert result is None

    def test_returns_none_when_generate_session_raises(self, monkeypatch):
        mock_api = MagicMock()
        mock_api.generateSession.side_effect = ConnectionError("network down")
        _patch_smart_connect(mock_api, monkeypatch)

        result = fetch_live_portfolio(**CREDS)
        assert result is None

    def test_returns_none_when_get_position_raises(self, monkeypatch):
        mock_api = _make_mock_smart_api()
        mock_api.getPosition.side_effect = RuntimeError("server error")
        _patch_smart_connect(mock_api, monkeypatch)

        result = fetch_live_portfolio(**CREDS)
        assert result is None

    def test_returns_none_when_session_status_false(self, monkeypatch):
        mock_api = MagicMock()
        mock_api.generateSession.return_value = None  # API returned nothing
        _patch_smart_connect(mock_api, monkeypatch)

        result = fetch_live_portfolio(**CREDS)
        assert result is None


# ---------------------------------------------------------------------------
# Happy-path / parsing tests
# ---------------------------------------------------------------------------

class TestHappyPath:

    def _raw_position(self, symbol="RELIANCE", qty="10", avg="2500.50",
                      ltp="2600.00", unrealised="995.00", daychangepct="1.5",
                      product="INTRADAY"):
        return {
            "tradingsymbol": symbol,
            "producttype": product,
            "netqty": qty,
            "averageprice": avg,
            "ltp": ltp,
            "unrealised": unrealised,
            "daychangepercentage": daychangepct,
        }

    def test_returns_account_summary(self, monkeypatch):
        mock_api = _make_mock_smart_api()
        _patch_smart_connect(mock_api, monkeypatch)

        result = fetch_live_portfolio(**CREDS)
        assert isinstance(result, AccountSummary)

    def test_account_fields_parsed_from_rms(self, monkeypatch):
        rms = {"net": "123456.78", "availablecash": "99000.00", "utilisedamount": "24456.78"}
        mock_api = _make_mock_smart_api(rms=rms)
        _patch_smart_connect(mock_api, monkeypatch)

        result = fetch_live_portfolio(**CREDS)
        assert result.net_value == pytest.approx(123456.78)
        assert result.available_cash == pytest.approx(99000.00)
        assert result.used_margin == pytest.approx(24456.78)

    def test_empty_positions_returns_summary_with_empty_list(self, monkeypatch):
        mock_api = _make_mock_smart_api(positions=[])
        _patch_smart_connect(mock_api, monkeypatch)

        result = fetch_live_portfolio(**CREDS)
        assert result.positions == []
        assert result.total_pnl_today == 0.0

    def test_positions_parsed_correctly(self, monkeypatch):
        raw = [
            self._raw_position("RELIANCE", qty="10", avg="2500.50", ltp="2600.00",
                               unrealised="995.00", daychangepct="1.5", product="INTRADAY"),
            self._raw_position("TCS", qty="5", avg="3800.00", ltp="3900.00",
                               unrealised="500.00", daychangepct="2.0", product="DELIVERY"),
        ]
        mock_api = _make_mock_smart_api(positions=raw)
        _patch_smart_connect(mock_api, monkeypatch)

        result = fetch_live_portfolio(**CREDS)
        assert len(result.positions) == 2

        rel = next(p for p in result.positions if p.symbol == "RELIANCE")
        assert rel.qty == 10
        assert rel.avg_price == pytest.approx(2500.50)
        assert rel.ltp == pytest.approx(2600.00)
        assert rel.pnl == pytest.approx(995.00)
        assert rel.day_change_pct == pytest.approx(1.5)
        assert rel.product_type == "INTRADAY"

        tcs = next(p for p in result.positions if p.symbol == "TCS")
        assert tcs.qty == 5
        assert tcs.product_type == "DELIVERY"

    def test_total_pnl_is_sum_of_position_pnl(self, monkeypatch):
        raw = [
            self._raw_position("RELIANCE", unrealised="1000.00"),
            self._raw_position("TCS", unrealised="-300.00"),
        ]
        mock_api = _make_mock_smart_api(positions=raw)
        _patch_smart_connect(mock_api, monkeypatch)

        result = fetch_live_portfolio(**CREDS)
        assert result.total_pnl_today == pytest.approx(700.00)

    def test_malformed_position_row_is_skipped(self, monkeypatch):
        """A position with a non-numeric qty should be skipped gracefully."""
        raw = [
            self._raw_position("RELIANCE"),
            {"tradingsymbol": "BADROW", "netqty": "NOT_A_NUMBER"},  # malformed
        ]
        mock_api = _make_mock_smart_api(positions=raw)
        _patch_smart_connect(mock_api, monkeypatch)

        result = fetch_live_portfolio(**CREDS)
        assert len(result.positions) == 1
        assert result.positions[0].symbol == "RELIANCE"

    def test_rms_failure_returns_zero_account_fields(self, monkeypatch):
        """If getRMS fails, account fields default to 0 but positions still parse."""
        mock_api = _make_mock_smart_api(
            positions=[self._raw_position("RELIANCE")],
            rms=None,
        )
        mock_api.getRMS.return_value = {"status": False, "message": "error"}
        _patch_smart_connect(mock_api, monkeypatch)

        result = fetch_live_portfolio(**CREDS)
        assert result is not None
        assert result.net_value == 0.0
        assert result.available_cash == 0.0
        assert result.used_margin == 0.0
        assert len(result.positions) == 1

    def test_zero_qty_position_is_included(self, monkeypatch):
        """Positions with netqty=0 (closed) are included so callers can filter."""
        raw = [self._raw_position("RELIANCE", qty="0")]
        mock_api = _make_mock_smart_api(positions=raw)
        _patch_smart_connect(mock_api, monkeypatch)

        result = fetch_live_portfolio(**CREDS)
        assert len(result.positions) == 1
        assert result.positions[0].qty == 0
