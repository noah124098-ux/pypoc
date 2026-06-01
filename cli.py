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
            log.error(
                "LIVE MODE BLOCKED: backtest gate has not passed or file is stale (>30 days). "
                "Reason: %s. Run: python cli.py walk-forward --years 3",
                reason,
            )
            raise SystemExit(2)
        log.info("Live-mode gate check passed.")
        log.warning(
            "LIVE MODE: AngelOneLiveBroker is a STUB. "
            "Do not use with real capital until implementation is complete. "
            "All order methods raise NotImplementedError."
        )

    if settings.capital.initial_inr < 50000:
        log.warning(
            "Initial capital INR %.0f is below the practical floor for Nifty 50 trading. "
            "Many stocks will be unbuyable and brokerage will dominate. "
            "Recommend INR 50,000+ for meaningful paper-trading.",
            settings.capital.initial_inr,
        )

    store = Store(settings.persistence.sqlite_path)
    if settings.mode == "live":
        try:
            from core.broker.angelone_live import AngelOneLiveBroker

            broker = AngelOneLiveBroker.from_env(settings.execution)
            log.warning(
                "AngelOneLiveBroker constructed — STUB, all order methods raise NotImplementedError. "
                "Falling through to PaperBroker until live implementation is complete."
            )
            # Fall back to PaperBroker because the stub raises on every call.
            broker = PaperBroker(settings.capital.initial_inr, settings.execution)
        except ValueError as exc:
            log.warning("Live broker creds not configured (%s); using PaperBroker.", exc)
            broker = PaperBroker(settings.capital.initial_inr, settings.execution)
    else:
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

    # --end-date pins the walk-forward end so results are reproducible regardless of run date.
    if args.end_date:
        try:
            fixed_end = datetime.strptime(args.end_date, "%Y-%m-%d")
        except ValueError:
            log.error("--end-date must be in YYYY-MM-DD format, got: %s", args.end_date)
            raise SystemExit(2)
        # Clamp to the latest available Nifty bar that is <= fixed_end
        available = nifty.index[nifty.index <= fixed_end.strftime("%Y-%m-%d")]
        if available.empty:
            log.error("No Nifty data available on or before %s", args.end_date)
            raise SystemExit(2)
        end = available[-1].to_pydatetime()
        log.info("Pinned end date to %s (latest bar on or before %s)", end.date(), args.end_date)
    else:
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
        bar = "#" * int(pct / 2)
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
            bar = "#" * int(pct / 3)
            print(f"  {k:<35}  {v:5d}  ({pct:5.1f}%)  {bar}")
    else:
        print("  (no rejections)")

    print(f"\n--- QTY=0 SIZING KILLS (before guardrails) ---")
    print(f"  qty_zero_count = {r.qty_zero_count}  "
          f"({'signals killed by sizing alone' if r.qty_zero_count else 'none -- sizing is not the bottleneck'})")

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

    # Per-strategy breakdown
    if r.trades:
        from collections import defaultdict
        strat_wins: dict[str, int] = defaultdict(int)
        strat_losses: dict[str, int] = defaultdict(int)
        strat_gross_win: dict[str, float] = defaultdict(float)
        strat_gross_loss: dict[str, float] = defaultdict(float)
        for t in r.trades:
            net = t.pnl - t.charges
            if net > 0:
                strat_wins[t.strategy] += 1
                strat_gross_win[t.strategy] += net
            else:
                strat_losses[t.strategy] += 1
                strat_gross_loss[t.strategy] += abs(net)
        all_strats = sorted(set(list(strat_wins.keys()) + list(strat_losses.keys())))
        if all_strats:
            print(f"\n--- PER-STRATEGY P&L BREAKDOWN ---")
            print(f"  {'Strategy':<28}  {'Trades':>6}  {'Win%':>5}  {'PF':>5}  {'Net P&L':>10}")
            for s in all_strats:
                w = strat_wins[s]; lo = strat_losses[s]
                total_s = w + lo
                win_pct = w / total_s * 100 if total_s else 0
                gw = strat_gross_win[s]; gl = strat_gross_loss[s]
                pf_s = gw / gl if gl else float("inf")
                net_s = gw - gl
                pf_str = f"{pf_s:.2f}" if pf_s != float("inf") else " inf"
                print(f"  {s:<28}  {total_s:>6}  {win_pct:>4.0f}%  {pf_str:>5}  {net_s:>10,.0f}")
    print()

    print("--- DIAGNOSIS HINTS ---")
    unknown_pct = unknown_days / total_days * 100 if total_days else 0
    if unknown_pct > 50:
        print(f"  [REGIME] {unknown_pct:.0f}% of days are UNKNOWN -- regime thresholds are too strict.")
        print(f"           Try: adx_trend_threshold: 20  and  bb_width_range_threshold: 0.06")
    if r.qty_zero_count > r.signal_count * 0.2:
        print(f"  [SIZING] {r.qty_zero_count} signals ({r.qty_zero_count/r.signal_count*100:.0f}%) "
              f"killed by qty=0 even at INR {debug_capital:,.0f}.")
        print(f"           Try: reducing atr_stop_multiplier (e.g. 1.5) or per_trade_risk_pct: 2.0")
    top_rule = max(r.rejection_breakdown.items(), key=lambda kv: kv[1])[0] if r.rejection_breakdown else None
    if top_rule and top_rule not in ("qty_zero_sizing", "stop_above_open_after_gap"):
        print(f"  [GUARDRAIL] Top rejection rule is '{top_rule}' -- investigate why it fires so often.")
    if r.signal_count == 0:
        print(f"  [SIGNAL] No signals at all -- strategies never fire given current regime + data.")
        print(f"           Run with --days 1825 (5y) to check if there's any historical period that trades.")
    print()


def cmd_profile_backtest(args):
    """Run a full backtest under cProfile and report the top 20 slowest functions."""
    import cProfile
    import io
    import pstats
    import time
    from datetime import timedelta

    from backtest.data_loader import HistoricalLoader
    from backtest.engine import BacktestEngine
    from backtest.metrics import compute_metrics
    from core.data.universe import resolve_universe

    settings = load_settings(args.config)
    setup_logging("WARNING", None)  # suppress INFO noise during profiling
    days = args.days or 365
    loader = HistoricalLoader()
    nifty = loader.load_nifty(days=days + 30)
    if nifty is None or nifty.empty:
        print("ERROR: Failed to load Nifty history. Check internet / yfinance.")
        raise SystemExit(2)

    symbols = resolve_universe(settings.universe.source, settings.universe.symbols)
    history = loader.load_universe(symbols, days=days + 30)
    print(f"Loaded {len(history)}/{len(symbols)} symbols, {len(nifty)} Nifty bars")

    end = nifty.index[-1].to_pydatetime()
    start = max(nifty.index[0].to_pydatetime(), end - timedelta(days=days))
    engine = BacktestEngine(settings)

    # ---- profile ----
    pr = cProfile.Profile()
    pr.enable()
    t0 = time.time()
    result = engine.run(
        symbol_history=history,
        nifty_history=nifty,
        starting_equity=settings.capital.initial_inr,
        start_date=start,
        end_date=end,
    )
    elapsed = time.time() - t0
    pr.disable()

    # ---- top-20 slowest functions ----
    s = io.StringIO()
    ps = pstats.Stats(pr, stream=s).sort_stats("cumulative")
    ps.print_stats(20)
    print("\n=== cProfile: Top 20 functions by cumulative time ===")
    print(s.getvalue())

    # ---- wall-clock / throughput ----
    print(f"Wall-clock time:       {elapsed:.2f}s")
    throughput = result.signal_count / elapsed if elapsed > 0 else 0
    print(f"Signals generated:     {result.signal_count}")
    print(f"Throughput:            {throughput:.0f} signals/sec")

    # ---- per-strategy cumulative time extracted from profiler ----
    print("\n=== Strategy evaluate() time (from profiler stats) ===")
    pr.create_stats()
    strategy_times: dict[str, float] = {}
    for func_key, stat in pr.stats.items():  # type: ignore[attr-defined]
        filename, lineno, funcname = func_key
        # stat layout: (cc, nc, tt, ct, callers)
        cumtime = stat[3]
        for strat in engine.strategies:
            if funcname == "evaluate" and strat.__class__.__name__.lower() in filename.lower():
                strategy_times[strat.name] = strategy_times.get(strat.name, 0) + cumtime
    if strategy_times:
        for sname, stime in sorted(strategy_times.items(), key=lambda kv: -kv[1]):
            print(f"  {sname:<30}  {stime*1000:.1f} ms cumulative")
    else:
        # Fallback: scan by module path fragment (strategies live in core/strategies/)
        for func_key, stat in pr.stats.items():  # type: ignore[attr-defined]
            filename, lineno, funcname = func_key
            if "strategies" in filename and funcname == "evaluate":
                # Use the module file name as proxy for strategy name
                mod = filename.rsplit("/", 1)[-1].rsplit("\\", 1)[-1].replace(".py", "")
                cumtime = stat[3]
                strategy_times[mod] = strategy_times.get(mod, 0) + cumtime
        if strategy_times:
            for sname, stime in sorted(strategy_times.items(), key=lambda kv: -kv[1]):
                print(f"  {sname:<30}  {stime*1000:.1f} ms cumulative")
        else:
            print("  (could not isolate per-strategy timings; see full profile above)")

    # ---- summary metrics ----
    metrics = compute_metrics(
        trades=result.trades,
        equity_curve=result.equity_curve,
        starting_equity=settings.capital.initial_inr,
        period_days=(result.period_end - result.period_start).days,
    )
    print(f"\n=== Backtest summary ===")
    print(f"Period:       {result.period_start.date()}  ->  {result.period_end.date()}")
    print(f"Trades:       {metrics.n_trades}")
    print(f"Sharpe:       {metrics.sharpe:.2f}")
    print(f"Max DD:       {metrics.max_drawdown_pct:.2f}%")


def cmd_benchmark_strategies(args):
    """Run each strategy alone on 1 year of RELIANCE data and report throughput."""
    import time
    from datetime import timedelta

    from backtest.data_loader import HistoricalLoader
    from backtest.engine import BacktestEngine
    from core.data.universe import resolve_universe
    from core.types import Regime

    settings = load_settings(args.config)
    setup_logging("WARNING", None)
    loader = HistoricalLoader()
    nifty = loader.load_nifty(days=365 + 30)

    # Load RELIANCE; fall back to the first symbol in the universe if not available.
    target_symbol = "RELIANCE"
    history = loader.load_universe([target_symbol], days=365 + 30)
    if target_symbol not in history:
        symbols = resolve_universe(settings.universe.source, settings.universe.symbols)
        history = loader.load_universe(symbols[:3], days=365 + 30)
        if not history:
            print("ERROR: Could not load benchmark data.")
            raise SystemExit(2)
        target_symbol = next(iter(history))
        print(f"RELIANCE not available; using {target_symbol} for benchmark")

    df = history[target_symbol]
    # Determine a plausible single regime to call evaluate() with.
    test_regimes = [Regime.TREND, Regime.RANGE, Regime.VOLATILE]

    engine = BacktestEngine(settings)
    if not engine.strategies:
        print("No strategies enabled in config. Nothing to benchmark.")
        raise SystemExit(2)

    print(f"\n=== Strategy benchmark on {target_symbol} ({len(df)} bars) ===")
    print(f"{'Strategy':<30}  {'Regime':<10}  {'Signals':>8}  {'μs/call':>8}  {'signals/s':>10}")
    print("-" * 72)

    ITERATIONS = max(100, len(df) - 30)  # one evaluate() call per rolling window position

    for strat in engine.strategies:
        best_signals = 0
        best_regime = None
        best_us_per_call = None
        best_sps = None

        for regime in test_regimes:
            if not strat.supports(regime):
                continue

            signals = 0
            t0 = time.perf_counter()
            calls = 0
            for idx in range(30, min(30 + ITERATIONS, len(df))):
                window = df.iloc[:idx]
                sig = strat.evaluate(target_symbol, window, regime)
                if sig is not None:
                    signals += 1
                calls += 1
            elapsed = time.perf_counter() - t0
            if calls == 0:
                continue

            us_per_call = elapsed / calls * 1_000_000
            sps = signals / elapsed if elapsed > 0 else 0

            if best_regime is None or signals > best_signals:
                best_signals = signals
                best_regime = regime
                best_us_per_call = us_per_call
                best_sps = sps

        if best_regime is None:
            print(f"  {strat.name:<30}  {'N/A':<10}  {'no supported regime':>30}")
        else:
            print(
                f"  {strat.name:<30}  {best_regime.value:<10}  "
                f"{best_signals:>8d}  {best_us_per_call:>8.1f}  {best_sps:>10.1f}"
            )

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
    wf.add_argument("--years", type=float, default=None, help="Number of years to cover (default: from config, typically 3)")
    wf.add_argument("--window-months", type=int, default=12, help="Size of each walk-forward window in months (default: 12)")
    wf.add_argument("--end-date", default=None, help="Pin the walk-forward end date YYYY-MM-DD for reproducible results (default: latest available data)")
    wf.set_defaults(func=cmd_walk_forward)

    sub.add_parser("check-gate").set_defaults(func=cmd_check_gate)

    dr = sub.add_parser(
        "debug-rejections",
        help="Re-run backtest at high capital and print full rejection breakdown + regime distribution",
    )
    dr.add_argument("--days", type=int, default=365, help="Look-back window in calendar days (default 365)")
    dr.add_argument(
        "--capital", type=float, default=500_000,
        help="Capital to use (default 500000 INR -- removes qty=0 noise from sizing)",
    )
    dr.set_defaults(func=cmd_debug_rejections)

    pb = sub.add_parser(
        "profile-backtest",
        help="Run a backtest under cProfile and report top-20 slowest functions + throughput",
    )
    pb.add_argument("--days", type=int, default=365, help="Look-back window in calendar days (default 365)")
    pb.set_defaults(func=cmd_profile_backtest)

    bs = sub.add_parser(
        "benchmark-strategies",
        help="Run each enabled strategy on 1 year of RELIANCE data and report signals/sec + us/call",
    )
    bs.set_defaults(func=cmd_benchmark_strategies)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
