"""All SQLite query functions for the dashboard."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd
import streamlit as st

DB_PATH = Path("data/agent.db")


def db_connect():
    """Open a connection to the agent SQLite database. Returns None if DB absent."""
    if not DB_PATH.exists():
        return None
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def query_df(conn, sql: str, params=()) -> pd.DataFrame:
    """Execute SQL and return a DataFrame; returns empty DataFrame on any error."""
    if conn is None:
        return pd.DataFrame()
    try:
        return pd.read_sql_query(sql, conn, params=params)
    except Exception:
        return pd.DataFrame()


@st.cache_data(ttl=30, show_spinner=False)
def get_equity_snapshots(db_path: str) -> pd.DataFrame:
    """Load equity snapshots from SQLite. Cached 30s; keyed by db_path."""
    if not Path(db_path).exists():
        return pd.DataFrame()
    try:
        c = sqlite3.connect(db_path)
        c.row_factory = sqlite3.Row
        df = pd.read_sql_query("SELECT ts, equity FROM equity_snapshots ORDER BY ts", c)
        c.close()
        return df
    except Exception:
        return pd.DataFrame()


@st.cache_data(ttl=30, show_spinner=False)
def get_trades(db_path: str) -> pd.DataFrame:
    """Load all closed trades from SQLite. Cached 30s; keyed by db_path."""
    if not Path(db_path).exists():
        return pd.DataFrame()
    try:
        c = sqlite3.connect(db_path)
        c.row_factory = sqlite3.Row
        df = pd.read_sql_query("SELECT * FROM trades ORDER BY closed_at DESC", c)
        c.close()
        return df
    except Exception:
        return pd.DataFrame()


@st.cache_data(ttl=30, show_spinner=False)
def get_signals(db_path: str, accepted_only: bool = False, limit: int = 200) -> pd.DataFrame:
    """Load recent signals from SQLite. Cached 30s; keyed by db_path/accepted_only/limit."""
    if not Path(db_path).exists():
        return pd.DataFrame()
    try:
        c = sqlite3.connect(db_path)
        c.row_factory = sqlite3.Row
        where = "WHERE accepted = 1" if accepted_only else ""
        df = pd.read_sql_query(
            f"SELECT * FROM signals {where} ORDER BY id DESC LIMIT {limit}", c
        )
        c.close()
        return df
    except Exception:
        return pd.DataFrame()
