"""CLI entry points.

Usage:
    python cli.py run --config config/default.yaml      # run live (paper) loop
    python cli.py warmup                                  # download history & build caches
    python cli.py check-config                            # validate YAML + .env
"""
from __future__ import annotations

import argparse
import time

from core.broker.paper import PaperBroker
from core.config import Secrets, load_settings
from core.data.angelone_feed import AngelOneFeed
from core.data.feed_base import ILiveFeed
from core.execution.orchestrator import Orchestrator
from core.logging_setup import JsonlEventLogger, setup_logging
from core.persistence.store import Store


def _build_feed(settings, secrets: Secrets) -> ILiveFeed:
    if settings.data.primary_feed == "angelone":
        return AngelOneFeed(
            api_key=secrets.angel_one_api_key,
            client_code=secrets.angel_one_client_code,
            password=secrets.angel_one_password,
            totp_secret=secrets.angel_one_totp_secret,
            reconnect_max_attempts=settings.data.reconnect_max_attempts,
            reconnect_backoff_seconds=settings.data.reconnect_backoff_seconds,
        )
    raise NotImplementedError(f"Feed {settings.data.primary_feed} not implemented yet")


def cmd_run(args):
    settings = load_settings(args.config)
    secrets = Secrets.from_env()
    log = setup_logging(settings.logging.level, settings.logging.file)
    events = JsonlEventLogger(settings.logging.json_log_file)

    # Strict gate: live mode requires a recent passing walk-forward run.
    if settings.mode == "live":
        from backtest.gate import is_live_allowed

        allowed, reason = is_live_allowed()
        if not allowed:
            log.error("Refusing to start in live mode: %s", reason)
            raise SystemExit(2)
        log.info("Live-mode gate check passed.")

    if settings.capital.initial_inr < 50000:
        log.warning(
            "Initial capital INR %.0f is below the practical floor for Nifty 50 trading. "
            "Many stocks will be unbuyable and brokerage will dominate. "
            "Recommend INR 50,000+ for meaningful paper-trading.",
            settings.capital.initial_inr,
        )

    store = Store(settings.persistence.sqlite_path)
    broker = PaperBroker(settings.capital.initial_inr, settings.execution)
    feed = _build_feed(settings, secrets)
    orch = Orchestrator(settings, feed, broker, store, events)

    orch.warmup()
    orch.start()

    log.info("Entering main tick lifecycle loop. Ctrl-C to stop.")
    try:
        while True:
            orch.tick_lifecycle()
            time.sleep(1)
    except KeyboardInterrupt:
        log.info("Shutting down ...")
        feed.disconnect()


def cmd_warmup(args):
    settings = load_settings(args.config)
    secrets = Secrets.from_env()
    setup_logging(settings.logging.level, settings.logging.file)
    Store(settings.persistence.sqlite_path)
    broker = PaperBroker(settings.capital.initial_inr, settings.execution)
    feed = _build_feed(settings, secrets) if secrets.angel_one_api_key else None
    events = JsonlEventLogger(settings.logging.json_log_file)
    if feed is None:
        # Build orchestrator without feed for offline warmup
        from core.data.feed_base import ILiveFeed

        class _Stub(ILiveFeed):
            def connect(self): ...
            def disconnect(self): ...
            def subscribe(self, symbols): ...
            def on_tick(self, cb): ...
            def is_connected(self): return False
            def last_tick_age_seconds(self): return float("inf")

        feed = _Stub()
    orch = Orchestrator(settings, feed, broker, Store(settings.persistence.sqlite_path), events)
    orch.warmup()
    print("Warmup complete.")


def cmd_check_config(args):
    settings = load_settings(args.config)
    secrets = Secrets.from_env()
    print(f"Mode:                 {settings.mode}")
    print(f"Capital:              INR {settings.capital.initial_inr:,.0f}")
    print(f"Universe source:      {settings.universe.source}")
    print(f"Primary data feed:    {settings.data.primary_feed}")
    print(f"Risk per trade:       {settings.risk.per_trade_risk_pct}%")
    print(f"Max open positions:   {settings.risk.max_open_positions}")
    print(f"Daily loss circuit:   -{settings.risk.daily_loss_circuit_pct}%")
    print()
    print("Credentials present:")
    print(f"  Angel One API key:  {'yes' if secrets.angel_one_api_key else 'NO (required for live data)'}")
    print(f"  Angel One client:   {'yes' if secrets.angel_one_client_code else 'NO'}")
    print(f"  Angel One TOTP:     {'yes' if secrets.angel_one_totp_secret else 'NO'}")
    print(f"  Anthropic API:      {'yes' if secrets.anthropic_api_key else 'no (EOD reviewer disabled)'}")
    print(f"  Telegram bot:       {'yes' if secrets.telegram_bot_token else 'no'}")


def cmd_mcp_server(args):
    import asyncio

    from mcp_server.server import main as mcp_main

    asyncio.run(mcp_main())


def cmd_backtest(args):
    from datetime import datetime

    from backtest.data_loader import HistoricalLoader
    from backtest.engine import BacktestEngine
    from backtest.metrics import compute_metrics
    from core.data.universe import resolve_universe

    settings = load_settings(args.config)
    log = setup_logging(settings.logging.level, settings.logging.file)
    days = args.days or 365
    loader = HistoricalLoader()
    nifty = loader.load_nifty(days=days + 30)
    if nifty is None or nifty.empty:
        log.error("Failed to load Nifty proxy history. Check internet / yfinance.")
        raise SystemExit(2)

    symbols = resolve_universe(settings.universe.source, settings.universe.symbols)
    history = loader.load_universe(symbols, days=days + 30)
    log.info("Loaded history for %d / %d symbols", len(history), len(symbols))

    end = nifty.index[-1].to_pydatetime()
    start = max(nifty.index[0].to_pydatetime(), (end - __import__("datetime").timedelta(days=days)))
    engine = BacktestEngine(settings)
    result = engine.run(
        symbol_history=history,
        nifty_history=nifty,
        starting_equity=settings.capital.initial_inr,
        start_date=start,
        end_date=end,
    )
    metrics = compute_metrics(
        trades=result.trades,
        equity_curve=result.equity_curve,
        starting_equity=settings.capital.initial_inr,
        period_days=(result.period_end - result.period_start).days,
    )
    print("\n=== Backtest result ===")
    print(f"Period:           {result.period_start.date()}  ->  {result.period_end.date()}")
    print(f"Starting equity:  INR {result.starting_equity:,.0f}")
    print(f"Ending equity:    INR {result.ending_equity:,.0f}")
    print(f"Trades:           {metrics.n_trades}")
    print(f"Win rate:         {metrics.win_rate_pct:.1f}%")
    print(f"Profit factor:    {metrics.profit_factor:.2f}")
    print(f"Sharpe:           {metrics.sharpe:.2f}")
    print(f"Sortino:          {metrics.sortino:.2f}")
    print(f"Max drawdown:     {metrics.max_drawdown_pct:.2f}%")
    print(f"CAGR:             {metrics.cagr_pct:.2f}%")
    print(f"Signals total:    {result.signal_count}  (accepted: {result.accepted_count}, rejected: {result.rejected_count})")
    if result.rejection_breakdown:
        print("Top rejection reasons:")
        for k, v in sorted(result.rejection_breakdown.items(), key=lambda kv: -kv[1])[:8]:
            print(f"  - {k}: {v}")


def cmd_walk_forward(args):
    from datetime import datetime, timedelta

    from backtest.data_loader import HistoricalLoader
    from backtest.gate import evaluate_gate, write_gate_result
    from backtest.walk_forward import run_walk_forward
    from core.data.universe import resolve_universe

    settings = load_settings(args.config)
    log = setup_logging(settings.logging.level, settings.logging.file)

    years = args.years or settings.backtest_gate.walk_forward_years
    days = int(years * 365) + 60
    loader = HistoricalLoader()
    nifty = loader.load_nifty(days=days)
    if nifty is None or nifty.empty:
        log.error("Failed to load Nifty proxy history.")
        raise SystemExit(2)

    symbols = resolve_universe(settings.universe.source, settings.universe.symbols)
    history = loader.load_universe(symbols, days=days)
    log.info("Loaded %d / %d symbols, %d Nifty days", len(history), len(symbols), len(nifty))

    end = nifty.index[-1].to_pydatetime()
    start = (end - timedelta(days=int(years * 365)))
    report = run_walk_forward(
        settings=settings,
        symbol_history=history,
        nifty_history=nifty,
        starting_equity=settings.capital.initial_inr,
        start_date=start,
        end_date=end,
        window_months=args.window_months or 12,
    )
    print(f"\n=== Walk-forward ({report.n_windows} windows) ===")
    for i, w in enumerate(report.windows, 1):
        m = w.metrics
        print(f"  W{i}: {w.start.date()} -> {w.end.date()}  trades={m.n_trades:4d} "
              f"sharpe={m.sharpe:5.2f} mdd={m.max_drawdown_pct:5.1f}% "
              f"win={m.win_rate_pct:5.1f}% pf={m.profit_factor:5.2f}")
    agg = report.aggregate_metrics
    print("\nAggregate:")
    print(f"  Trades:        {agg.n_trades}")
    print(f"  Win rate:      {agg.win_rate_pct:.1f}%")
    print(f"  Profit factor: {agg.profit_factor:.2f}")
    print(f"  Sharpe:        {agg.sharpe:.2f}")
    print(f"  Max DD:        {agg.max_drawdown_pct:.2f}%")
    print(f"  CAGR:          {agg.cagr_pct:.2f}%")

    gate = evaluate_gate(
        agg, settings.backtest_gate,
        period_start=start, period_end=end, walk_forward_years=years,
    )
    write_gate_result(gate)
    print()
    print(f"Gate: {'PASSED' if gate.passed else 'FAILED'}")
    for c in gate.checks:
        op = ">=" if c.direction == "ge" else "<="
        marker = "OK " if c.pass_ else "X  "
        print(f"  {marker} {c.name:20s} {c.actual:8.2f} {op} {c.threshold:8.2f}")
    if gate.failures:
        print(f"  Failures: {gate.failures}")


def cmd_debug_rejections(args):
    """Re-run backtest at high capital and print a full diagnostic breakdown.

    Answers: why is the agent generating so few trades? Pinpoints whether the
    bottleneck is regime filtering, position sizing (qty=0), or a specific
    guardrail rule.
    """
    from datetime import timedelta

    from backtest.data_loader import HistoricalLoader
    from backtest.engine import BacktestEngine
    from backtest.metrics import compute_metrics
    from core.config import load_settings
    from core.data.universe import resolve_universe

    settings = load_settings(args.config)

    # Override capital to a realistic floor so qty=0 rejections vanish from sizing noise.
    debug_capital = args.capital
    settings.capital.initial_inr = debug_capital

    log = setup_logging("WARNING", None)   # suppress INFO spam during debug run
    days = args.days
    loader = HistoricalLoader()
    nifty = loader.load_nifty(days=days + 30)
    if nifty is None or nifty.empty:
        print("ERROR: Failed to load Nifty history. Check internet / yfinance.")
        raise SystemExit(2)

    symbols = resolve_universe(settings.universe.source, settings.universe.symbols)
    history = loader.load_universe(symbols, days=days + 30)

    end = nifty.index[-1].to_pydatetime()
    start = end - timedelta(days=days)
    engine = BacktestEngine(settings)
    r = engine.run(
        symbol_history=history,
        nifty_history=nifty,
        starting_equity=debug_capital,
        start_date=start,
        end_date=end,
    )
    m = compute_metrics(
        trades=r.trades,
        equity_curve=r.equity_curve,
        starting_equity=debug_capital,
        period_days=(r.period_end - r.period_start).days,
    )

    total_days = sum(r.regime_distribution.values())
    unknown_days = r.regime_distribution.get("UNKNOWN", 0)
    trading_days = total_days - unknown_days

    print(f"\n{'='*60}")
    print(f"  DEBUG REJECTION REPORT  ({r.period_start.date()} -> {r.period_end.date()})")
    print(f"  Capital: INR {debug_capital:,.0f}")
    print(f"{'='*60}")

    print(f"\n--- REGIME DISTRIBUTION ({total_days} days) ---")
    for regime in ["TREND", "RANGE", "VOLATILE", "UNKNOWN"]:
        count = r.regime_distribution.get(regime, 0)
        pct = count / total_days * 100 if total_days else 0
        bar = "█" * int(pct / 2)
        print(f"  {regime:<10} {count:5d} days  ({pct:5.1f}%)  {bar}")
    if unknown_days:
        print(f"  *** {unknown_days} UNKNOWN days = no strategies run on those days ***")

    print(f"\n--- SIGNAL FUNNEL ---")
    print(f"  Trading days (non-UNKNOWN):  {trading_days}")
    print(f"  Signals generated:           {r.signal_count}")
    print(f"  Signals accepted (filled):   {r.accepted_count}  (trades = {m.n_trades})")
    print(f"  Signals rejected:            {r.rejected_count}")
    if trading_days:
        print(f"  Signals/trading day:         {r.signal_count / trading_days:.2f}")

    print(f"\n--- SIGNALS BY STRATEGY ---")
    for strat, count in sorted(r.signal_count_by_strategy.items(), key=lambda kv: -kv[1]):
        acc = r.accepted_count_by_strategy.get(strat, 0)
        rej = count - acc
        print(f"  {strat:<28}  generated={count:4d}  accepted={acc:3d}  rejected={rej:4d}")

    print(f"\n--- REJECTION BREAKDOWN (all rejections = {r.rejected_count}) ---")
    if r.rejection_breakdown:
        for k, v in sorted(r.rejection_breakdown.items(), key=lambda kv: -kv[1]):
            pct = v / r.rejected_count * 100 if r.rejected_count else 0
            bar = "█" * int(pct / 3)
            print(f"  {k:<35}  {v:5d}  ({pct:5.1f}%)  {bar}")
    else:
        print("  (no rejections)")

    print(f"\n--- QTY=0 SIZING KILLS (before guardrails) ---")
    print(f"  qty_zero_count = {r.qty_zero_count}  "
          f"({'signals killed by sizing alone' if r.qty_zero_count else 'none — sizing is not the bottleneck'})")

    print(f"\n--- TOP SIGNAL-GENERATING SYMBOLS (top 15) ---")
    top_syms = sorted(r.signal_count_by_symbol.items(), key=lambda kv: -kv[1])[:15]
    for sym, cnt in top_syms:
        print(f"  {sym:<20}  {cnt:4d} signals")

    print(f"\n--- METRICS AT INR {debug_capital:,.0f} CAPITAL ---")
    print(f"  Trades:        {m.n_trades}")
    print(f"  Win rate:      {m.win_rate_pct:.1f}%")
    print(f"  Profit factor: {m.profit_factor:.2f}")
    print(f"  Sharpe:        {m.sharpe:.2f}")
    print(f"  Max DD:        {m.max_drawdown_pct:.2f}%")
    print(f"  CAGR:          {m.cagr_pct:.2f}%")
    print()

    print("--- DIAGNOSIS HINTS ---")
    unknown_pct = unknown_days / total_days * 100 if total_days else 0
    if unknown_pct > 50:
        print(f"  [REGIME] {unknown_pct:.0f}% of days are UNKNOWN — regime thresholds are too strict.")
        print(f"           Try: adx_trend_threshold: 20  and  bb_width_range_threshold: 0.06")
    if r.qty_zero_count > r.signal_count * 0.2:
        print(f"  [SIZING] {r.qty_zero_count} signals ({r.qty_zero_count/r.signal_count*100:.0f}%) "
              f"killed by qty=0 even at INR {debug_capital:,.0f}.")
        print(f"           Try: reducing atr_stop_multiplier (e.g. 1.5) or per_trade_risk_pct: 2.0")
    top_rule = max(r.rejection_breakdown.items(), key=lambda kv: kv[1])[0] if r.rejection_breakdown else None
    if top_rule and top_rule not in ("qty_zero_sizing", "stop_above_open_after_gap"):
        print(f"  [GUARDRAIL] Top rejection rule is '{top_rule}' — investigate why it fires so often.")
    if r.signal_count == 0:
        print(f"  [SIGNAL] No signals at all — strategies never fire given current regime + data.")
        print(f"           Run with --days 1825 (5y) to check if there's any historical period that trades.")
    print()


def cmd_check_gate(args):
    from backtest.gate import is_live_allowed, read_gate_result

    data = read_gate_result()
    allowed, reason = is_live_allowed()
    if data is None:
        print("No gate file. Run `python cli.py walk-forward` first.")
        raise SystemExit(2)
    print(f"Gate file timestamp:  {data.get('timestamp')}")
    print(f"Period:               {data.get('period_start')}  ->  {data.get('period_end')}")
    print(f"Passed:               {data.get('passed')}")
    print(f"Live-mode allowed:    {allowed}  ({reason})")
    if data.get("failures"):
        print(f"Failures:             {data['failures']}")


def main():
    parser = argparse.ArgumentParser(prog="nse-agent")
    parser.add_argument("--config", default="config/default.yaml")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("run").set_defaults(func=cmd_run)
    sub.add_parser("warmup").set_defaults(func=cmd_warmup)
    sub.add_parser("check-config").set_defaults(func=cmd_check_config)
    sub.add_parser("mcp-server").set_defaults(func=cmd_mcp_server)

    bt = sub.add_parser("backtest", help="Run a single-window backtest over the last N days")
    bt.add_argument("--days", type=int, default=365)
    bt.set_defaults(func=cmd_backtest)

    wf = sub.add_parser("walk-forward", help="Run walk-forward and write data/backtest_gate.json")
    wf.add_argument("--years", type=float, default=None)
    wf.add_argument("--window-months", type=int, default=12)
    wf.set_defaults(func=cmd_walk_forward)

    sub.add_parser("check-gate").set_defaults(func=cmd_check_gate)

    dr = sub.add_parser(
        "debug-rejections",
        help="Re-run backtest at high capital and print full rejection breakdown + regime distribution",
    )
    dr.add_argument("--days", type=int, default=365, help="Look-back window in calendar days (default 365)")
    dr.add_argument(
        "--capital", type=float, default=500_000,
        help="Capital to use (default 500000 INR — removes qty=0 noise from sizing)",
    )
    dr.set_defaults(func=cmd_debug_rejections)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
