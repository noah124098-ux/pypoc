"""CLI entry points.

Usage:
    python cli.py run --config config/default.yaml      # run live (paper) loop
    python cli.py warmup                                  # download history & build caches
    python cli.py check-config                            # validate YAML + .env
"""
from __future__ import annotations

import argparse
import signal
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
    settings = load_settings(args.config, env=getattr(args, "env", None))
    secrets = Secrets.from_env()
    log = setup_logging(
        settings.logging.level,
        settings.logging.file,
        max_bytes=settings.logging.max_bytes,
        backup_count=settings.logging.backup_count,
    )
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

    # --- Graceful shutdown: SIGINT / SIGTERM both halt the orchestrator cleanly ---
    orch: "Orchestrator | None" = None  # forward-declared; set after construction below

    def _shutdown_handler(sig: int, frame: object) -> None:
        log.info("Shutdown signal received (%s)", signal.Signals(sig).name)
        if orch is not None:
            orch.halted = True
            orch.halt_reason = f"shutdown signal {signal.Signals(sig).name}"

    signal.signal(signal.SIGINT, _shutdown_handler)
    signal.signal(signal.SIGTERM, _shutdown_handler)

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
        while not orch.halted:
            orch.tick_lifecycle()
            time.sleep(1)
        log.info("Orchestrator halted (%s). Disconnecting feed.", orch.halt_reason)
    except KeyboardInterrupt:
        log.info("Shutting down ...")
    finally:
        feed.disconnect()


def cmd_warmup(args):
    settings = load_settings(args.config, env=getattr(args, "env", None))
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
    settings = load_settings(args.config, env=getattr(args, "env", None))
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
    print(f"  Upstox API key:     {'yes' if secrets.upstox_api_key else 'no (optional — alternative feed)'}")
    print(f"  Upstox access tok:  {'yes' if secrets.upstox_access_token else 'no'}")
    print(f"  Anthropic API:      {'yes' if secrets.anthropic_api_key else 'no (EOD reviewer disabled)'}")
    print(f"  Telegram bot:       {'yes' if secrets.telegram_bot_token else 'no'}")

    # --- Config validation warnings ---
    warnings: list[str] = []

    # Warn if Angel One API key looks like a placeholder
    _placeholder_patterns = ("your_", "your_api_key_here", "<", "xxx", "test", "dummy",
                              "placeholder", "changeme", "example", "sk-ant-...")
    for field_name, value in [
        ("ANGEL_ONE_API_KEY", secrets.angel_one_api_key),
        ("ANGEL_ONE_API_SECRET", secrets.angel_one_api_secret),
        ("ANGEL_ONE_CLIENT_CODE", secrets.angel_one_client_code),
        ("ANGEL_ONE_PASSWORD", secrets.angel_one_password),
        ("ANGEL_ONE_TOTP_SECRET", secrets.angel_one_totp_secret),
    ]:
        if value:
            vl = value.lower()
            if any(p in vl for p in _placeholder_patterns):
                warnings.append(
                    f"WARNING: {field_name} looks like a placeholder value "
                    f"('{value[:20]}...') — copy .env.example to .env and fill in real credentials."
                )

    # Warn if ANTHROPIC_API_KEY is empty (EOD reviewer won't work)
    if not secrets.anthropic_api_key:
        warnings.append(
            "WARNING: ANTHROPIC_API_KEY is not set — EOD reviewer will be disabled. "
            "Set it in .env to enable daily trade analysis."
        )

    # Warn if mode=live but gate not passed
    if settings.mode == "live":
        from backtest.gate import is_live_allowed
        allowed, reason = is_live_allowed()
        if not allowed:
            warnings.append(
                f"WARNING: mode=live but backtest gate has not passed or is stale. "
                f"Reason: {reason}. Run: python cli.py walk-forward --years 3"
            )

    # Warn if ANGEL_ONE_LIVE_* matches ANGEL_ONE_* (credential confusion)
    live_fields = {
        "ANGEL_ONE_LIVE_API_KEY": secrets.angel_one_live_api_key,
        "ANGEL_ONE_LIVE_CLIENT_CODE": secrets.angel_one_live_client_code,
        "ANGEL_ONE_LIVE_PASSWORD": secrets.angel_one_live_password,
        "ANGEL_ONE_LIVE_TOTP_SECRET": secrets.angel_one_live_totp_secret,
    }
    data_fields = {
        "ANGEL_ONE_API_KEY": secrets.angel_one_api_key,
        "ANGEL_ONE_CLIENT_CODE": secrets.angel_one_client_code,
        "ANGEL_ONE_PASSWORD": secrets.angel_one_password,
        "ANGEL_ONE_TOTP_SECRET": secrets.angel_one_totp_secret,
    }
    confused: list[str] = []
    for (lk, lv), (dk, dv) in zip(live_fields.items(), data_fields.items()):
        if lv and dv and lv == dv:
            confused.append(f"{lk} == {dk}")
    if confused:
        warnings.append(
            "SECURITY WARNING: ANGEL_ONE_LIVE_* credentials match ANGEL_ONE_* data-feed credentials. "
            "These MUST be separate Angel One API apps. "
            f"Matching fields: {', '.join(confused)}. "
            "Create a new Angel One app for live order execution."
        )

    if warnings:
        print()
        for w in warnings:
            print(w)


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

    settings = load_settings(args.config, env=getattr(args, "env", None))
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

    settings = load_settings(args.config, env=getattr(args, "env", None))
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

    settings = load_settings(args.config, env=getattr(args, "env", None))

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

    settings = load_settings(args.config, env=getattr(args, "env", None))
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

    settings = load_settings(args.config, env=getattr(args, "env", None))
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
    import json as _json

    from backtest.gate import GATE_MAX_AGE_DAYS, is_live_allowed, read_gate_result
    from datetime import datetime, timezone

    data = read_gate_result()
    allowed, reason = is_live_allowed()
    if data is None:
        if getattr(args, "json", False):
            print(_json.dumps({"error": "no gate file"}, indent=2))
        else:
            print("No gate file. Run `python cli.py walk-forward` first.")
        raise SystemExit(2)

    if getattr(args, "json", False):
        print(_json.dumps(data, indent=2, default=str))
        return

    # Compute gate age in days
    ts_str = data.get("timestamp", "")
    gate_age_days: float | None = None
    if ts_str:
        try:
            ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            gate_age_days = (datetime.now(timezone.utc) - ts).total_seconds() / 86400
        except ValueError:
            pass

    age_str = f"{gate_age_days:.0f} days old" if gate_age_days is not None else "unknown age"
    passed = data.get("passed", False)
    if passed and gate_age_days is not None and gate_age_days <= GATE_MAX_AGE_DAYS:
        gate_label = f"PASSED ({age_str})"
    elif passed:
        gate_label = f"EXPIRED ({age_str})"
    else:
        gate_label = "FAILED"

    print(f"Gate file timestamp:  {data.get('timestamp')}")
    print(f"Period:               {data.get('period_start')}  ->  {data.get('period_end')}")
    print(f"Passed:               {gate_label}")
    print(f"Live-mode allowed:    {allowed}  ({reason})")
    if data.get("failures"):
        print(f"Failures:             {data['failures']}")

    # ------------------------------------------------------------------
    # Optional live broker connection check (--check-live)
    # ------------------------------------------------------------------
    if getattr(args, "check_live", False):
        from dotenv import load_dotenv
        import os

        load_dotenv(override=False)
        live_api_key = os.getenv("ANGEL_ONE_LIVE_API_KEY", "")
        live_client_code = os.getenv("ANGEL_ONE_LIVE_CLIENT_CODE", "")
        live_password = os.getenv("ANGEL_ONE_LIVE_PASSWORD", "")
        live_totp_secret = os.getenv("ANGEL_ONE_LIVE_TOTP_SECRET", "")
        data_feed_key = os.getenv("ANGEL_ONE_API_KEY", "")

        missing = [
            k for k, v in {
                "ANGEL_ONE_LIVE_API_KEY": live_api_key,
                "ANGEL_ONE_LIVE_CLIENT_CODE": live_client_code,
                "ANGEL_ONE_LIVE_PASSWORD": live_password,
                "ANGEL_ONE_LIVE_TOTP_SECRET": live_totp_secret,
            }.items() if not v
        ]

        print()
        print("Live broker connection check (--check-live):")
        if missing:
            print(
                f"  SKIP — ANGEL_ONE_LIVE_* credentials incomplete "
                f"(missing: {', '.join(missing)})"
            )
        elif live_api_key == data_feed_key and data_feed_key:
            print(
                "  FAIL — ANGEL_ONE_LIVE_API_KEY matches ANGEL_ONE_API_KEY. "
                "These must be separate Angel One API apps."
            )
        else:
            try:
                from core.broker.angelone_live import AngelOneLiveBroker
                from core.config import load_settings

                settings = load_settings(args.config, env=getattr(args, "env", None))
                broker = AngelOneLiveBroker.from_env(settings.execution)
                broker.connect()
                try:
                    rms = broker._smart_api.getRMS()
                    if rms and rms.get("status"):
                        data_rms = rms.get("data") or {}
                        cash = data_rms.get("availablecash", "?")
                        net = data_rms.get("net", "?")
                        print(
                            f"  OK — session established; getRMS() success "
                            f"(availablecash={cash}, net={net})"
                        )
                    else:
                        msg = (rms or {}).get("message", "unknown")
                        print(f"  WARN — session established but getRMS() returned non-success: {msg}")
                except Exception as rms_exc:
                    print(f"  WARN — session established but getRMS() raised: {rms_exc}")
                finally:
                    try:
                        broker.disconnect()
                    except Exception:
                        pass
            except RuntimeError as exc:
                print(f"  FAIL — generateSession failed: {exc}")
            except ValueError as exc:
                print(f"  FAIL — credential error: {exc}")
            except Exception as exc:
                print(f"  FAIL — unexpected error: {type(exc).__name__}: {exc}")


def cmd_status(args):
    """Quick status check — reads snapshot.json and prints a compact live overview."""
    import json as _json
    import os
    from datetime import datetime, timezone

    from backtest.gate import GATE_MAX_AGE_DAYS, is_live_allowed, read_gate_result

    settings = load_settings(args.config, env=getattr(args, "env", None))
    snapshot_path = getattr(settings, "snapshot_path", "data/snapshot.json")

    # Read snapshot
    from core.runtime_snapshot import read as read_snapshot
    snap = read_snapshot(snapshot_path)

    print("=== NSE Agent Status ===")

    if snap is None:
        print("Snapshot:     not found — agent may not be running")
        print(f"              (expected at {snapshot_path})")
    else:
        mode = snap.get("mode", "paper")
        pid = snap.get("pid")
        halted = snap.get("halted", False)
        halt_reason = snap.get("halt_reason", "")

        # Uptime from snapshot timestamp
        ts_str = snap.get("ts", "")
        uptime_str = "?"
        if ts_str:
            try:
                ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                age_s = (datetime.now(timezone.utc) - ts).total_seconds()
                uptime_str = f"{age_s:.0f}s ago"
            except ValueError:
                pass

        # PID uptime from OS if we have pid and agent.pid file
        pid_file = "data/agent.pid"
        running_str = "Unknown"
        if pid:
            try:
                # Check if process is alive (works on Windows and Linux)
                os.kill(pid, 0)
                running_str = f"Yes (PID {pid})"
            except (OSError, ProcessLookupError):
                running_str = f"No (PID {pid} not running)"

        equity = float(snap.get("equity") or 0.0)
        starting_equity_today = float(snap.get("starting_equity_today") or equity)
        day_pnl = equity - starting_equity_today
        day_pnl_pct = (day_pnl / starting_equity_today * 100) if starting_equity_today else 0.0

        regime = snap.get("current_regime", "UNKNOWN")
        open_pos = snap.get("open_positions") or []
        tick_age = snap.get("last_tick_age_seconds")
        tick_str = f"{tick_age:.0f}s ago" if tick_age is not None else "?"

        halt_suffix = f"  [HALTED: {halt_reason}]" if halted else ""

        # Format equity in Indian notation
        def _fmt_inr(v: float) -> str:
            s = str(int(abs(v)))
            if len(s) <= 3:
                formatted = s
            else:
                last3 = s[-3:]
                rest = s[:-3]
                groups = []
                while len(rest) > 2:
                    groups.append(rest[-2:])
                    rest = rest[:-2]
                if rest:
                    groups.append(rest)
                groups.reverse()
                formatted = ",".join(groups) + "," + last3
            sign = "-" if v < 0 else ""
            return f"{sign}₹{formatted}"

        pnl_sign = "+" if day_pnl >= 0 else ""
        print(f"Mode:         {mode}{halt_suffix}")
        print(f"Running:      {running_str}")
        print(f"Equity:       {_fmt_inr(equity)} ({pnl_sign}{_fmt_inr(day_pnl)} today, {pnl_sign}{day_pnl_pct:.2f}%)")
        print(f"Regime:       {regime}")
        print(f"Positions:    {len(open_pos)} open")
        print(f"Last tick:    {tick_str}")
        print(f"Snapshot at:  {uptime_str}")

    # Gate status
    gate_data = read_gate_result()
    if gate_data is None:
        gate_str = "NO FILE (run walk-forward)"
    else:
        from datetime import datetime, timezone
        passed = gate_data.get("passed", False)
        ts_str = gate_data.get("timestamp", "")
        gate_age_days: float | None = None
        if ts_str:
            try:
                ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                gate_age_days = (datetime.now(timezone.utc) - ts).total_seconds() / 86400
            except ValueError:
                pass
        age_label = f"{gate_age_days:.0f} days old" if gate_age_days is not None else ""
        if passed and gate_age_days is not None and gate_age_days <= GATE_MAX_AGE_DAYS:
            gate_str = f"PASSED ({age_label})"
        elif passed:
            gate_str = f"EXPIRED ({age_label})"
        else:
            failures = gate_data.get("failures", [])
            gate_str = f"FAILED: {', '.join(failures)}"
    print(f"Gate:         {gate_str}")


def cmd_performance(args):
    """Print a formatted performance report using generate_eod_report()."""
    from core.analytics.performance_report import generate_eod_report

    settings = load_settings(args.config, env=getattr(args, "env", None))
    db_path = settings.persistence.sqlite_path
    snapshot_path = getattr(settings, "snapshot_path", "data/snapshot.json")
    days = args.days

    # generate_eod_report uses last-30-days internally; we note the requested window
    report = generate_eod_report(db_path, snapshot_path)

    # Prepend a header if days != 30 (the default used internally)
    if days != 30:
        header = f"=== Performance Report (last {days} days -- note: trade detail uses last 30 days from DB) ==="
    else:
        header = f"=== Performance Report (last {days} days) ==="

    # Write via UTF-8 to handle Rupee symbol on Windows terminals with legacy codepages.
    import sys
    if hasattr(sys.stdout, "buffer"):
        _write = sys.stdout.buffer.write
        def _uprint(s: str) -> None:
            _write((s + "\n").encode("utf-8", errors="replace"))
    else:
        def _uprint(s: str) -> None:  # type: ignore[misc]
            print(s)

    _uprint(header)
    _uprint(report)


def cmd_strategy_report(args):
    """Print per-strategy metrics from the SQLite trade history."""
    from pathlib import Path

    from core.analytics.metrics import compute_strategy_attribution, load_trades_from_db

    settings = load_settings(args.config, env=getattr(args, "env", None))
    db_path = settings.persistence.sqlite_path
    days = args.days

    if not Path(db_path).exists():
        print(f"No database found at {db_path}. Run the agent first to generate trades.")
        raise SystemExit(2)

    from datetime import datetime, timedelta, timezone
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    all_trades = load_trades_from_db(db_path)
    trades = [t for t in all_trades if t.closed_at.replace(tzinfo=timezone.utc) >= cutoff
              or t.closed_at >= cutoff.replace(tzinfo=None)]

    if not trades:
        print(f"No closed trades in the last {days} days.")
        return

    attribution = compute_strategy_attribution(trades)

    print(f"=== Strategy Report (last {days} days — {len(trades)} trades) ===")
    print()
    header = f"{'Strategy':<24}  {'Trades':>6}  {'Win%':>5}  {'PF':>5}  {'Sharpe':>6}  {'Net P&L':>12}"
    print(header)
    print("-" * len(header))

    def _fmt_pnl(v: float) -> str:
        s = str(int(abs(v)))
        if len(s) <= 3:
            formatted = s
        else:
            last3 = s[-3:]
            rest = s[:-3]
            groups = []
            while len(rest) > 2:
                groups.append(rest[-2:])
                rest = rest[:-2]
            if rest:
                groups.append(rest)
            groups.reverse()
            formatted = ",".join(groups) + "," + last3
        sign = "+" if v >= 0 else "-"
        return f"{sign}₹{formatted}"

    for strategy, m in sorted(attribution.items()):
        pf_str = f"{m.profit_factor:.2f}" if m.profit_factor != float("inf") else "  inf"
        print(
            f"{strategy:<24}  {m.n_trades:>6}  {m.win_rate_pct:>4.1f}%  "
            f"{pf_str:>5}  {m.sharpe:>6.2f}  {_fmt_pnl(m.total_pnl):>12}"
        )
    print()

    # Totals row
    total_trades = sum(m.n_trades for m in attribution.values())
    total_pnl = sum(m.total_pnl for m in attribution.values())
    print(f"{'TOTAL':<24}  {total_trades:>6}  {'':>5}  {'':>5}  {'':>6}  {_fmt_pnl(total_pnl):>12}")


def cmd_schedule_gate_refresh(args):
    """Register a Windows Task Scheduler entry that runs refresh_gate.bat every Sunday at 06:00."""
    import os
    import subprocess
    import sys

    repo_root = os.path.dirname(os.path.abspath(__file__))
    bat_path = os.path.join(repo_root, "scripts", "refresh_gate.bat")

    if not os.path.isfile(bat_path):
        print(f"ERROR: refresh_gate.bat not found at {bat_path}")
        raise SystemExit(2)

    task_name = "NSE-Gate-Refresh"
    cmd = [
        "schtasks", "/create",
        "/tn", task_name,
        "/tr", bat_path,
        "/sc", "weekly",
        "/d", "sun",
        "/st", "06:00",
        "/f",
    ]

    print(f"Registering Task Scheduler entry '{task_name}'...")
    print(f"  Script: {bat_path}")
    print(f"  Schedule: Every Sunday at 06:00")

    try:
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode == 0:
            print(f"SUCCESS: Task '{task_name}' created.")
            if result.stdout.strip():
                print(result.stdout.strip())
        else:
            print(f"ERROR: schtasks exited {result.returncode}")
            if result.stderr.strip():
                print(result.stderr.strip())
            if result.stdout.strip():
                print(result.stdout.strip())
            raise SystemExit(result.returncode)
    except FileNotFoundError:
        print("ERROR: 'schtasks' not found. This command requires Windows.")
        raise SystemExit(2)


def cmd_preflight(args):
    """Run all pre-flight checks before starting the live paper-trading agent.

    Exit code 0 = all checks pass (safe to run).
    Exit code 1 = one or more checks failed (fix before running).
    """
    import json as _json
    import os
    import subprocess
    import sys
    from datetime import datetime, timezone
    from pathlib import Path

    PASS = "✅"  # ✅
    FAIL = "❌"  # ❌

    # UTF-8-safe print (Windows terminals may use cp1252 which can't encode these chars)
    if hasattr(sys.stdout, "buffer"):
        _write = sys.stdout.buffer.write

        def _uprint(s: str) -> None:
            _write((s + "\n").encode("utf-8", errors="replace"))
    else:
        def _uprint(s: str) -> None:  # type: ignore[misc]
            print(s)

    results: list[tuple[bool, str]] = []

    def check(label: str, ok: bool, detail: str = "") -> bool:
        marker = PASS if ok else FAIL
        suffix = f"  ({detail})" if detail else ""
        _uprint(f"  {marker} {label}{suffix}")
        results.append((ok, label))
        return ok

    _uprint("\n=== NSE Agent Pre-flight Check ===\n")

    # ------------------------------------------------------------------
    # 1. Virtual environment active
    # ------------------------------------------------------------------
    in_venv = (
        os.getenv("VIRTUAL_ENV") is not None
        or hasattr(sys, "real_prefix")
        or (hasattr(sys, "base_prefix") and sys.base_prefix != sys.prefix)
    )
    check("1. Virtual environment active", in_venv,
          "" if in_venv else "activate with: .venv\\Scripts\\Activate.ps1")

    # ------------------------------------------------------------------
    # 2. Angel One credentials present
    # ------------------------------------------------------------------
    from dotenv import load_dotenv
    load_dotenv(override=False)
    creds_required = {
        "ANGEL_ONE_API_KEY": os.getenv("ANGEL_ONE_API_KEY", ""),
        "ANGEL_ONE_CLIENT_CODE": os.getenv("ANGEL_ONE_CLIENT_CODE", ""),
        "ANGEL_ONE_PASSWORD": os.getenv("ANGEL_ONE_PASSWORD", ""),
        "ANGEL_ONE_TOTP_SECRET": os.getenv("ANGEL_ONE_TOTP_SECRET", ""),
    }
    missing_creds = [k for k, v in creds_required.items() if not v]
    creds_present = len(missing_creds) == 0
    check("2. Angel One credentials present (.env has ANGEL_ONE_API_KEY etc.)",
          creds_present,
          f"missing: {', '.join(missing_creds)}" if missing_creds else "")

    # ------------------------------------------------------------------
    # 3. Angel One credentials have correct format (not placeholder values)
    # ------------------------------------------------------------------
    PLACEHOLDER_PATTERNS = {"your_", "<", "xxx", "test", "dummy", "placeholder", "changeme"}
    bad_creds = []
    for k, v in creds_required.items():
        if v:
            vl = v.lower()
            if any(p in vl for p in PLACEHOLDER_PATTERNS):
                bad_creds.append(k)
    creds_format_ok = creds_present and len(bad_creds) == 0
    check("3. Angel One credentials have correct format (not placeholder values)",
          creds_format_ok,
          f"likely placeholder: {', '.join(bad_creds)}" if bad_creds else (
              "credentials missing (see check 2)" if not creds_present else ""))

    # ------------------------------------------------------------------
    # 4. Backtest gate passes (check data/backtest_gate.json, not expired)
    # ------------------------------------------------------------------
    from backtest.gate import GATE_MAX_AGE_DAYS, is_live_allowed, read_gate_result

    gate_data = read_gate_result()
    gate_allowed, gate_reason = is_live_allowed()
    if gate_data is None:
        gate_detail = "no gate file — run: python cli.py walk-forward --years 3"
    elif not gate_data.get("passed", False):
        failures = gate_data.get("failures", [])
        gate_detail = f"FAILED: {', '.join(failures)}"
    else:
        ts_str = gate_data.get("timestamp", "")
        gate_age_days: float | None = None
        if ts_str:
            try:
                ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                gate_age_days = (datetime.now(timezone.utc) - ts).total_seconds() / 86400
            except ValueError:
                pass
        if gate_age_days is not None and gate_age_days > GATE_MAX_AGE_DAYS:
            gate_detail = f"EXPIRED ({gate_age_days:.0f} days old > {GATE_MAX_AGE_DAYS} day limit)"
        else:
            age_label = f"{gate_age_days:.0f} days old" if gate_age_days is not None else ""
            gate_detail = age_label
    check("4. Backtest gate passes (data/backtest_gate.json, not expired)",
          gate_allowed, gate_detail)

    # ------------------------------------------------------------------
    # 5. Config validates (load_settings succeeds)
    # ------------------------------------------------------------------
    config_ok = False
    config_detail = ""
    try:
        load_settings(args.config, env=getattr(args, "env", None))
        config_ok = True
    except Exception as exc:
        config_detail = str(exc)[:120]
    check("5. Config validates (python cli.py check-config succeeds)",
          config_ok, config_detail)

    # ------------------------------------------------------------------
    # 6. Tests pass (pytest -q --tb=no, timeout 60s)
    # ------------------------------------------------------------------
    tests_ok = False
    tests_detail = ""
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "--tb=no"],
            capture_output=True, text=True, timeout=60,
        )
        output = (proc.stdout + proc.stderr).strip()
        # Last line typically: "N passed in Xs"
        summary_line = output.split("\n")[-1] if output else ""
        if proc.returncode == 0:
            tests_ok = True
            tests_detail = summary_line
        else:
            tests_detail = summary_line or f"exit code {proc.returncode}"
    except subprocess.TimeoutExpired:
        tests_detail = "timed out after 60s"
    except Exception as exc:
        tests_detail = str(exc)[:80]
    check("6. Tests pass (pytest -q --tb=no)", tests_ok, tests_detail)

    # ------------------------------------------------------------------
    # 7. Data directory exists and is writable
    # ------------------------------------------------------------------
    data_dir = Path("data")
    data_exists = data_dir.exists() and data_dir.is_dir()
    data_writable = False
    if data_exists:
        try:
            probe = data_dir / ".preflight_write_probe"
            probe.write_text("ok")
            probe.unlink()
            data_writable = True
        except OSError:
            pass
    data_ok = data_exists and data_writable
    if not data_exists:
        data_detail = "directory does not exist — run: mkdir data"
    elif not data_writable:
        data_detail = "directory is not writable"
    else:
        data_detail = str(data_dir.resolve())
    check("7. Data directory exists and is writable (data/)", data_ok, data_detail)

    # ------------------------------------------------------------------
    # 8. Logs directory exists
    # ------------------------------------------------------------------
    logs_dir = Path("logs")
    logs_ok = logs_dir.exists() and logs_dir.is_dir()
    logs_detail = "" if logs_ok else "directory does not exist — run: mkdir logs"
    check("8. Logs directory exists (logs/)", logs_ok, logs_detail)

    # ------------------------------------------------------------------
    # 9. Market hours check — is NSE open right now? (IST 09:15–15:30 weekdays)
    # ------------------------------------------------------------------
    from zoneinfo import ZoneInfo

    ist_tz = ZoneInfo("Asia/Kolkata")
    now_ist = datetime.now(ist_tz)
    weekday = now_ist.weekday()  # 0=Mon ... 6=Sun
    market_open_time = now_ist.replace(hour=9, minute=15, second=0, microsecond=0)
    market_close_time = now_ist.replace(hour=15, minute=30, second=0, microsecond=0)
    is_weekday = weekday < 5
    is_open_hours = market_open_time <= now_ist <= market_close_time
    market_open = is_weekday and is_open_hours
    if not is_weekday:
        market_detail = f"today is {now_ist.strftime('%A')} — market closed on weekends"
    elif now_ist < market_open_time:
        opens_in = (market_open_time - now_ist).seconds // 60
        market_detail = f"pre-market — opens in {opens_in}m (IST {now_ist.strftime('%H:%M')})"
    elif now_ist > market_close_time:
        market_detail = f"post-market (IST {now_ist.strftime('%H:%M')}, closed at 15:30)"
    else:
        market_detail = f"NSE open (IST {now_ist.strftime('%H:%M')})"
    # Market hours is a WARNING not a hard failure — you may want to start before open
    check("9. Market hours — is NSE open right now? (IST 09:15-15:30 weekdays)",
          market_open, market_detail)

    # ------------------------------------------------------------------
    # 10. Snapshot freshness — if agent.pid exists, is snapshot.json recent (<60s)?
    # ------------------------------------------------------------------
    pid_file = Path("data/agent.pid")
    snapshot_file = Path("data/snapshot.json")
    snap_ok = True
    snap_detail = ""
    if pid_file.exists():
        # Agent may be running — check snapshot freshness
        if not snapshot_file.exists():
            snap_ok = False
            snap_detail = "agent.pid exists but data/snapshot.json missing"
        else:
            try:
                snap_data = _json.loads(snapshot_file.read_text(encoding="utf-8"))
                ts_str = snap_data.get("ts", "")
                if ts_str:
                    ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                    age_s = (datetime.now(timezone.utc) - ts).total_seconds()
                    if age_s > 60:
                        snap_ok = False
                        snap_detail = f"snapshot is {age_s:.0f}s old (>60s) — agent may be stuck"
                    else:
                        snap_detail = f"snapshot {age_s:.0f}s old — agent running OK"
                else:
                    snap_detail = "snapshot has no timestamp field"
            except (OSError, _json.JSONDecodeError, ValueError) as exc:
                snap_ok = False
                snap_detail = f"could not read snapshot: {exc}"
    else:
        snap_detail = "no agent.pid — agent is not currently running"
    check("10. Snapshot freshness (if agent.pid exists, snapshot.json < 60s old)",
          snap_ok, snap_detail)

    # ------------------------------------------------------------------
    # 15. ANGEL_ONE_LIVE_* credentials present (required for live mode)
    # ------------------------------------------------------------------
    settings_for_mode = None
    try:
        settings_for_mode = load_settings(args.config, env=getattr(args, "env", None))
    except Exception:
        pass

    current_mode = getattr(settings_for_mode, "mode", "paper") if settings_for_mode else "paper"
    live_creds = {
        "ANGEL_ONE_LIVE_API_KEY": os.getenv("ANGEL_ONE_LIVE_API_KEY", ""),
        "ANGEL_ONE_LIVE_CLIENT_CODE": os.getenv("ANGEL_ONE_LIVE_CLIENT_CODE", ""),
        "ANGEL_ONE_LIVE_PASSWORD": os.getenv("ANGEL_ONE_LIVE_PASSWORD", ""),
        "ANGEL_ONE_LIVE_TOTP_SECRET": os.getenv("ANGEL_ONE_LIVE_TOTP_SECRET", ""),
    }
    missing_live_creds = [k for k, v in live_creds.items() if not v]
    data_feed_key = os.getenv("ANGEL_ONE_API_KEY", "")
    live_api_key = live_creds.get("ANGEL_ONE_LIVE_API_KEY", "")
    cross_contaminated = bool(
        live_api_key and data_feed_key and live_api_key == data_feed_key
    )
    if current_mode == "live":
        # Hard requirement in live mode: all four creds must be present and not cross-contaminated.
        live_creds_ok = (len(missing_live_creds) == 0) and (not cross_contaminated)
        if missing_live_creds:
            live_creds_detail = f"missing: {', '.join(missing_live_creds)}"
        elif cross_contaminated:
            live_creds_detail = (
                "SECURITY: ANGEL_ONE_LIVE_API_KEY == ANGEL_ONE_API_KEY — must be a SEPARATE app"
            )
        else:
            live_creds_detail = "all ANGEL_ONE_LIVE_* vars present and distinct from data-feed key"
    else:
        # In paper mode, live creds are optional — warn if missing but do not fail.
        live_creds_ok = True
        if missing_live_creds:
            live_creds_detail = (
                f"not set (OK for paper mode) — required before switching to mode=live: "
                f"{', '.join(missing_live_creds)}"
            )
        elif cross_contaminated:
            live_creds_ok = False
            live_creds_detail = (
                "SECURITY: ANGEL_ONE_LIVE_API_KEY == ANGEL_ONE_API_KEY — must be a SEPARATE app"
            )
        else:
            live_creds_detail = "ANGEL_ONE_LIVE_* vars present and distinct from data-feed key"
    check(
        "15. ANGEL_ONE_LIVE_* credentials present (required for live mode)",
        live_creds_ok,
        live_creds_detail,
    )

    # ------------------------------------------------------------------
    # 16. AngelOneLiveBroker can connect (attempt generateSession)
    #     Only run if --check-live flag is passed.
    # ------------------------------------------------------------------
    if getattr(args, "check_live", False):
        live_connect_ok = False
        live_connect_detail = ""
        if missing_live_creds:
            live_connect_detail = (
                f"skipped — ANGEL_ONE_LIVE_* credentials incomplete "
                f"(missing: {', '.join(missing_live_creds)})"
            )
            live_connect_ok = False
        elif cross_contaminated:
            live_connect_detail = "skipped — cross-contamination guard would block connection"
            live_connect_ok = False
        else:
            try:
                from core.broker.angelone_live import AngelOneLiveBroker
                from core.config import ExecutionCfg

                # Build a minimal ExecutionCfg from settings if available.
                if settings_for_mode is not None:
                    exec_cfg = settings_for_mode.execution
                else:
                    exec_cfg = ExecutionCfg(
                        slippage_bps=5.0,
                        brokerage_per_order_inr=20.0,
                        stt_pct=0.1,
                        exchange_txn_pct=0.00345,
                        gst_pct=18.0,
                        signal_cooldown_minutes=60,
                    )

                broker = AngelOneLiveBroker.from_env(exec_cfg)
                broker.connect()
                # Verify a JWT token was returned by getRMS (read-only).
                try:
                    rms = broker._smart_api.getRMS()
                    token_ok = bool(rms and rms.get("status"))
                    if token_ok:
                        live_connect_ok = True
                        live_connect_detail = "session established; getRMS() returned success"
                    else:
                        live_connect_detail = (
                            f"session established but getRMS() returned non-success: "
                            f"{(rms or {}).get('message', 'unknown')}"
                        )
                except Exception as rms_exc:
                    live_connect_detail = (
                        f"session established but getRMS() raised: {rms_exc}"
                    )
                finally:
                    try:
                        broker.disconnect()
                    except Exception:
                        pass
            except RuntimeError as exc:
                live_connect_detail = f"generateSession failed: {exc}"
            except ValueError as exc:
                live_connect_detail = f"credential error: {exc}"
            except Exception as exc:
                live_connect_detail = f"unexpected error: {type(exc).__name__}: {exc}"
        check(
            "16. AngelOneLiveBroker can connect (generateSession + getRMS verified)",
            live_connect_ok,
            live_connect_detail,
        )

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------
    n_fail = sum(1 for ok, _ in results if not ok)
    _uprint("")
    if n_fail == 0:
        _uprint(f"  {PASS} All checks passed — safe to run: python cli.py run")
    else:
        _uprint(f"  {FAIL} {n_fail} check{'s' if n_fail != 1 else ''} failed — fix before running")
    _uprint("")

    raise SystemExit(0 if n_fail == 0 else 1)


def cmd_health_check(args):
    """Lightweight health check for Docker HEALTHCHECK and monitoring.

    Validates that core modules import cleanly and config loads without errors.
    Exits 0 on success, 1 on any failure. Produces minimal stdout output.

    With --json: outputs a machine-readable JSON object to stdout.
    Without --json: outputs human-readable text (default).
    """
    import json as _json
    import os
    import sys
    from datetime import datetime, timezone

    use_json = getattr(args, "json", False)
    checks_failed: list[str] = []

    # 1. Config loads
    settings = None
    try:
        settings = load_settings(args.config, env=getattr(args, "env", None))
    except Exception as exc:
        checks_failed.append(f"config: {exc}")

    # 2. Core module imports (sanity check that the install is intact)
    for mod in ["core.regime.classifier", "core.risk.guardrails", "core.strategies.trend_breakout"]:
        try:
            __import__(mod)
        except Exception as exc:
            checks_failed.append(f"import {mod}: {exc}")

    healthy = len(checks_failed) == 0

    if not use_json:
        # --- Human-readable output (existing behaviour) ---
        if checks_failed:
            for msg in checks_failed:
                print(f"FAIL: {msg}", file=sys.stderr)
            raise SystemExit(1)
        print("OK")
        return

    # --- Machine-readable JSON output ---
    snapshot_age_seconds: float | None = None
    agent_running: bool = False
    halted: bool = False
    equity: float | None = None
    open_positions: int = 0

    snapshot_path = "data/snapshot.json"
    if settings is not None:
        snapshot_path = getattr(settings, "snapshot_path", snapshot_path)

    try:
        from core.runtime_snapshot import read as read_snapshot
        snap = read_snapshot(snapshot_path)
        if snap is not None:
            ts_str = snap.get("ts", "")
            if ts_str:
                try:
                    ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                    snapshot_age_seconds = round(
                        (datetime.now(timezone.utc) - ts).total_seconds(), 1
                    )
                except ValueError:
                    pass
            halted = bool(snap.get("halted", False))
            equity_raw = snap.get("equity")
            if equity_raw is not None:
                equity = float(equity_raw)
            open_pos = snap.get("open_positions") or []
            open_positions = len(open_pos)

            pid = snap.get("pid")
            if pid:
                try:
                    os.kill(pid, 0)
                    agent_running = True
                except (OSError, ProcessLookupError):
                    agent_running = False
    except Exception:
        pass

    # Gate status
    gate_passed: bool = False
    gate_age_days: int | None = None
    try:
        from backtest.gate import GATE_MAX_AGE_DAYS, read_gate_result
        gate_data = read_gate_result()
        if gate_data is not None:
            gate_passed = bool(gate_data.get("passed", False))
            ts_str = gate_data.get("timestamp", "")
            if ts_str:
                try:
                    ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                    age_days = (datetime.now(timezone.utc) - ts).total_seconds() / 86400
                    gate_age_days = int(age_days)
                    # Treat as not-passed if expired
                    if age_days > GATE_MAX_AGE_DAYS:
                        gate_passed = False
                except ValueError:
                    pass
    except Exception:
        pass

    payload: dict = {
        "status": "healthy" if healthy else "unhealthy",
        "snapshot_age_seconds": snapshot_age_seconds,
        "agent_running": agent_running,
        "halted": halted,
        "gate_passed": gate_passed,
        "gate_age_days": gate_age_days,
        "equity": equity,
        "open_positions": open_positions,
    }
    if not healthy:
        payload["errors"] = checks_failed

    print(_json.dumps(payload, indent=2))
    raise SystemExit(0 if healthy else 1)


def cmd_send_command(args):
    """Send a command to the running agent via the file-based command queue.

    Supported command types:
      reload-config               Trigger an immediate config reload (risk params only)
      halt                        Halt the agent
      resume                      Resume the agent
    """
    from core.command_queue import enqueue

    cmd_type_map = {
        "reload-config": "reload_config",
        "halt": "halt_agent",
        "resume": "resume_agent",
    }

    raw_type = args.command_type
    cmd_type = cmd_type_map.get(raw_type, raw_type.replace("-", "_"))

    params: dict = {}
    if args.reason:
        params["reason"] = args.reason

    cmd = enqueue(cmd_type, params)
    print(f"Command queued: type={cmd.type}  id={cmd.id}")
    print(f"The running agent will process it within ~1 second.")


def main():
    parser = argparse.ArgumentParser(prog="nse-agent")
    parser.add_argument("--config", default="config/default.yaml")
    parser.add_argument(
        "--env",
        default=None,
        choices=["dev", "staging", "prod"],
        help="Environment config overlay (default: APP_ENV env var; if unset, base config only — no overlay)",
    )
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

    cg = sub.add_parser("check-gate", help="Check the walk-forward gate result")
    cg.add_argument("--json", action="store_true", help="Output raw gate.json as formatted JSON")
    cg.add_argument(
        "--check-live",
        action="store_true",
        dest="check_live",
        help="Attempt a read-only getRMS() call to verify live broker connection",
    )
    cg.set_defaults(func=cmd_check_gate)

    sub.add_parser(
        "status",
        help="Quick live status: equity, regime, positions, gate, last tick",
    ).set_defaults(func=cmd_status)

    perf = sub.add_parser(
        "performance",
        help="Print a formatted EOD performance report from the DB + snapshot",
    )
    perf.add_argument("--days", type=int, default=30, help="Look-back window in days (default 30)")
    perf.set_defaults(func=cmd_performance)

    sr = sub.add_parser(
        "strategy-report",
        help="Per-strategy metrics (trades, win%, profit factor, Sharpe, P&L)",
    )
    sr.add_argument("--days", type=int, default=90, help="Look-back window in days (default 90)")
    sr.set_defaults(func=cmd_strategy_report)

    sub.add_parser(
        "schedule-gate-refresh",
        help="Register a Windows Task Scheduler entry to run refresh_gate.bat every Sunday at 06:00",
    ).set_defaults(func=cmd_schedule_gate_refresh)

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

    pf = sub.add_parser(
        "preflight",
        help="Run all pre-flight checks before starting live paper trading",
    )
    pf.add_argument(
        "--check-live",
        action="store_true",
        dest="check_live",
        help=(
            "Also run check 16: attempt AngelOneLiveBroker.connect() + getRMS() "
            "to verify live broker credentials are valid (requires ANGEL_ONE_LIVE_* in .env)"
        ),
    )
    pf.set_defaults(func=cmd_preflight)

    hc = sub.add_parser(
        "health-check",
        help="Lightweight health check (used by Docker HEALTHCHECK): exits 0 if healthy",
    )
    hc.add_argument(
        "--json",
        action="store_true",
        help="Output machine-readable JSON instead of plain text (for Docker / monitoring)",
    )
    hc.set_defaults(func=cmd_health_check)

    sc = sub.add_parser(
        "send-command",
        help="Send a command to the running agent via the file-based command queue",
    )
    sc.add_argument(
        "command_type",
        choices=["reload-config", "halt", "resume"],
        help="Command to send: reload-config | halt | resume",
    )
    sc.add_argument("--reason", default="", help="Optional reason string (for halt/resume)")
    sc.set_defaults(func=cmd_send_command)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
