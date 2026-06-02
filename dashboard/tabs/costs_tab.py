"""Costs tab — trade costs and charge breakdown."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from dashboard.utils.charts import color_pnl
from dashboard.utils.db import query_df


def render(snap: dict, config: dict, conn) -> None:
    """Render the Trade Costs & Charge Breakdown tab."""
    st.header("Trade Costs & Charge Breakdown")

    exec_cfg = config.get("execution", {})
    _brokerage_per_order = float(exec_cfg.get("brokerage_per_order_inr", 20.0))
    _stt_pct = float(exec_cfg.get("stt_pct", 0.025))
    _exchange_txn_pct = float(exec_cfg.get("exchange_txn_pct", 0.00345))
    _gst_pct = float(exec_cfg.get("gst_pct", 18.0))

    # Load all trades
    costs_trades_df = query_df(conn, "SELECT * FROM trades ORDER BY closed_at DESC")

    if not costs_trades_df.empty and "charges" in costs_trades_df.columns:
        import pandas as pd
        total_charges_alltime = float(costs_trades_df["charges"].sum())
        total_gross_pnl = float(costs_trades_df["pnl"].sum()) if "pnl" in costs_trades_df.columns else 0.0
        total_net_pnl = total_gross_pnl - total_charges_alltime
        avg_charge_per_trade = total_charges_alltime / len(costs_trades_df) if len(costs_trades_df) else 0.0
        charges_pct_of_gross = (total_charges_alltime / abs(total_gross_pnl) * 100
                                if total_gross_pnl != 0 else 0.0)

        costs_trades_df["closed_at_dt"] = pd.to_datetime(costs_trades_df["closed_at"], errors="coerce")
        today_str = datetime.utcnow().date()
        today_df = costs_trades_df[costs_trades_df["closed_at_dt"].dt.date == today_str]
        today_charges = float(today_df["charges"].sum()) if not today_df.empty else 0.0

        # Top metrics
        mc1, mc2, mc3, mc4 = st.columns(4)
        mc1.metric("Total Charges (all-time)", f"₹{total_charges_alltime:,.0f}")
        mc2.metric("Charges Today", f"₹{today_charges:,.0f}")
        mc3.metric("Avg Charge / Trade", f"₹{avg_charge_per_trade:,.0f}")
        mc4.metric("Charges as % of Gross P&L", f"{charges_pct_of_gross:.1f}%")

        st.markdown(
            f"**Net P&L = Gross ₹{total_gross_pnl:,.0f} − ₹{total_charges_alltime:,.0f} charges = "
            f"₹{total_net_pnl:,.0f}**"
        )

        st.divider()

        # Charges per strategy (bar chart)
        if "strategy" in costs_trades_df.columns:
            st.subheader("Charges by Strategy")
            strat_costs = (
                costs_trades_df.groupby("strategy")
                .agg(total_charges=("charges", "sum"), trades=("charges", "count"),
                     gross_pnl=("pnl", "sum"))
                .reset_index()
            )
            strat_costs["net_pnl"] = strat_costs["gross_pnl"] - strat_costs["total_charges"]
            strat_costs["avg_charge"] = strat_costs["total_charges"] / strat_costs["trades"]

            fig_strat = go.Figure()
            fig_strat.add_trace(go.Bar(
                x=strat_costs["strategy"], y=strat_costs["total_charges"],
                name="Total Charges", marker_color="#e67e22",
                text=strat_costs["total_charges"].apply(lambda v: f"₹{v:,.0f}"),
                textposition="outside",
            ))
            fig_strat.update_layout(
                height=320, margin=dict(l=0, r=0, t=20, b=0),
                yaxis_title="Charges (INR)", xaxis_title="Strategy",
            )
            st.plotly_chart(fig_strat, use_container_width=True)

            st.dataframe(
                strat_costs.rename(columns={
                    "strategy": "Strategy", "trades": "Trades",
                    "total_charges": "Total Charges (₹)", "gross_pnl": "Gross P&L (₹)",
                    "net_pnl": "Net P&L (₹)", "avg_charge": "Avg Charge/Trade (₹)",
                }).style.format({
                    "Total Charges (₹)": "₹{:,.0f}",
                    "Gross P&L (₹)": "₹{:,.0f}",
                    "Net P&L (₹)": "₹{:,.0f}",
                    "Avg Charge/Trade (₹)": "₹{:,.0f}",
                }).applymap(color_pnl, subset=["Net P&L (₹)", "Gross P&L (₹)"]),
                use_container_width=True, hide_index=True,
            )

        st.divider()

        # Charge breakdown pie chart
        st.subheader("Estimated Charge Component Breakdown")
        st.caption(
            "Breakdown is estimated from config rates applied to average trade size. "
            "Exact per-component values are not stored individually in the database."
        )

        if len(costs_trades_df) > 0 and "qty" in costs_trades_df.columns and "exit_price" in costs_trades_df.columns:
            from core.broker.charges import compute_charges as _compute_charges
            from core.types import Side

            sample = costs_trades_df[["qty", "exit_price"]].dropna()
            if not sample.empty:
                avg_qty = int(sample["qty"].median())
                avg_price = float(sample["exit_price"].median())
                avg_qty = max(avg_qty, 1)
                avg_price = max(avg_price, 1.0)

                bd = _compute_charges(
                    side=Side.SELL,
                    qty=avg_qty,
                    price=avg_price,
                    brokerage_per_order_inr=_brokerage_per_order,
                    stt_pct=_stt_pct,
                    exchange_txn_pct=_exchange_txn_pct,
                    gst_pct=_gst_pct,
                )
                component_total = bd.total if bd.total > 0 else 1.0
                pie_labels = ["Brokerage", "STT", "Exchange Txn", "GST", "SEBI", "Stamp Duty", "Clearing"]
                pie_values_raw = [
                    bd.brokerage, bd.stt, bd.exchange_txn, bd.gst,
                    bd.sebi, bd.stamp_duty, bd.clearing,
                ]
                scale = total_charges_alltime / component_total if component_total else 1.0
                pie_values = [v * scale for v in pie_values_raw]

                fig_pie = px.pie(
                    names=pie_labels, values=pie_values,
                    title=f"Estimated breakdown of ₹{total_charges_alltime:,.0f} total charges",
                    color_discrete_sequence=px.colors.qualitative.Set2,
                )
                fig_pie.update_layout(height=380, margin=dict(l=0, r=0, t=50, b=10))
                st.plotly_chart(fig_pie, use_container_width=True)

                breakdown_df = pd.DataFrame({
                    "Component": pie_labels,
                    "Estimated Amount (₹)": [f"₹{v:,.0f}" for v in pie_values],
                    "% of Total": [f"{v / total_charges_alltime * 100:.1f}%" if total_charges_alltime else "—"
                                   for v in pie_values],
                })
                st.dataframe(breakdown_df, use_container_width=True, hide_index=True)
            else:
                st.info("Not enough trade data to estimate charge breakdown.")
        else:
            st.info("Not enough trade data for charge breakdown. Need qty and exit_price columns.")

        st.divider()

        # Recent 20 trades with charges
        st.subheader("Recent 20 Trades — Per-Trade Charges")
        import pandas as pd
        recent_cols = [c for c in
            ["closed_at", "symbol", "strategy", "side", "qty", "entry_price", "exit_price",
             "pnl", "charges", "exit_reason"]
            if c in costs_trades_df.columns]
        recent_20 = costs_trades_df[recent_cols].head(20)
        if "pnl" in recent_20.columns and "charges" in recent_20.columns:
            recent_20 = recent_20.copy()
            recent_20["net_pnl"] = recent_20["pnl"] - recent_20["charges"]
            pnl_cols_20 = [c for c in ["pnl", "net_pnl"] if c in recent_20.columns]
            fmt_map = {c: "₹{:.2f}" for c in ["entry_price", "exit_price", "pnl", "charges", "net_pnl"]
                       if c in recent_20.columns}
            styled_20 = recent_20.style.applymap(color_pnl, subset=pnl_cols_20).format(fmt_map)
            st.dataframe(styled_20, use_container_width=True, height=350)
        else:
            st.dataframe(recent_20, use_container_width=True, height=350)

    else:
        st.info("No closed trades in the database yet. Run the agent to record trades.")

    st.divider()

    # Estimated exit charges for current open positions
    st.subheader("Estimated Exit Charges — Open Positions")
    open_positions = snap.get("open_positions", [])
    if open_positions:
        from core.broker.charges import compute_charges as _compute_charges_pos
        from core.types import Side as _Side
        import pandas as pd

        pos_charge_rows = []
        total_est_charges = 0.0
        for pos in open_positions:
            try:
                qty = int(pos.get("qty", 0))
                last_price = float(pos.get("last_price") or pos.get("avg_price", 0))
                if qty <= 0 or last_price <= 0:
                    continue
                bd = _compute_charges_pos(
                    side=_Side.SELL,
                    qty=qty,
                    price=last_price,
                    brokerage_per_order_inr=_brokerage_per_order,
                    stt_pct=_stt_pct,
                    exchange_txn_pct=_exchange_txn_pct,
                    gst_pct=_gst_pct,
                )
                total_est_charges += bd.total
                pos_charge_rows.append({
                    "Symbol": pos.get("symbol", "?"),
                    "Strategy": pos.get("strategy", "?"),
                    "Qty": qty,
                    "Last Price (₹)": last_price,
                    "Turnover (₹)": qty * last_price,
                    "Est. Exit Charges (₹)": bd.total,
                    "Brokerage": bd.brokerage,
                    "STT": bd.stt,
                    "Exchange Txn": bd.exchange_txn,
                    "GST": bd.gst,
                })
            except Exception:
                continue

        if pos_charge_rows:
            pos_charges_df = pd.DataFrame(pos_charge_rows)
            fmt_pos = {c: "₹{:,.2f}" for c in pos_charges_df.columns if c not in ("Symbol", "Strategy", "Qty")}
            st.dataframe(
                pos_charges_df.style.format(fmt_pos),
                use_container_width=True, hide_index=True,
            )
            st.metric(
                "Total estimated exit charges if all positions closed now",
                f"₹{total_est_charges:,.0f}",
            )
        else:
            st.info("Could not compute charges — check position data has qty and last_price.")
    else:
        st.info("No open positions to estimate exit charges for.")
