"""Nifty 50 universe. Symbol list as of late 2025; refresh periodically from NSE."""
from __future__ import annotations

NIFTY_50: list[str] = [
    "ADANIENT", "ADANIPORTS", "APOLLOHOSP", "ASIANPAINT", "AXISBANK",
    "BAJAJ-AUTO", "BAJFINANCE", "BAJAJFINSV", "BEL", "BHARTIARTL",
    "BPCL", "BRITANNIA", "CIPLA", "COALINDIA", "DRREDDY",
    "EICHERMOT", "GRASIM", "HCLTECH", "HDFCBANK", "HDFCLIFE",
    "HEROMOTOCO", "HINDALCO", "HINDUNILVR", "ICICIBANK", "INDUSINDBK",
    "INFY", "ITC", "JSWSTEEL", "KOTAKBANK", "LT",
    "M&M", "MARUTI", "NESTLEIND", "NTPC", "ONGC",
    "POWERGRID", "RELIANCE", "SBILIFE", "SBIN", "SHRIRAMFIN",
    "SUNPHARMA", "TATACONSUM", "TATAMOTORS", "TATASTEEL", "TCS",
    "TECHM", "TITAN", "TRENT", "ULTRACEMCO", "WIPRO",
]


def resolve_universe(source: str, override_symbols: list[str]) -> list[str]:
    if override_symbols:
        return override_symbols
    if source == "nifty50":
        return list(NIFTY_50)
    if source == "nifty200":
        return list(NIFTY_200)
    raise ValueError(f"Unknown universe source: {source}")

# Nifty 200 constituents — NSE official list (ind_nifty200list.csv), fetched 2026-06.
# DUMMY* placeholder entries (pending corporate actions) excluded. Recent IPOs with
# <3y history are skipped automatically by the backtest engine (needs 55+ bars).
NIFTY_200: list[str] = [
    "360ONE", "ABB", "ABCAPITAL", "ADANIENSOL", "ADANIENT",
    "ADANIGREEN", "ADANIPORTS", "ADANIPOWER", "ALKEM", "AMBUJACEM",
    "APLAPOLLO", "APOLLOHOSP", "ASHOKLEY", "ASIANPAINT", "ASTRAL",
    "ATGL", "AUBANK", "AUROPHARMA", "AXISBANK", "BAJAJ-AUTO",
    "BAJAJFINSV", "BAJAJHLDNG", "BAJFINANCE", "BANKBARODA", "BANKINDIA",
    "BDL", "BEL", "BHARATFORG", "BHARTIARTL", "BHEL",
    "BIOCON", "BLUESTARCO", "BOSCHLTD", "BPCL", "BRITANNIA",
    "BSE", "CANBK", "CGPOWER", "CHOLAFIN", "CIPLA",
    "COALINDIA", "COCHINSHIP", "COFORGE", "COLPAL", "CONCOR",
    "COROMANDEL", "CUMMINSIND", "DABUR", "DIVISLAB", "DIXON",
    "DLF", "DMART", "DRREDDY", "EICHERMOT", "ENRIN",
    "ETERNAL", "EXIDEIND", "FEDERALBNK", "FORTIS", "GAIL",
    "GLENMARK", "GMRAIRPORT", "GODFRYPHLP", "GODREJCP", "GODREJPROP",
    "GRASIM", "GROWW", "GVT&D", "HAL", "HAVELLS",
    "HCLTECH", "HDFCAMC", "HDFCBANK", "HDFCLIFE", "HEROMOTOCO",
    "HINDALCO", "HINDPETRO", "HINDUNILVR", "HINDZINC", "HUDCO",
    "HYUNDAI", "ICICIAMC", "ICICIBANK", "ICICIGI", "IDEA",
    "IDFCFIRSTB", "INDHOTEL", "INDIANB", "INDIGO", "INDUSINDBK",
    "INDUSTOWER", "INFY", "IOC", "IRCTC", "IREDA",
    "IRFC", "ITC", "JINDALSTEL", "JIOFIN", "JSWENERGY",
    "JSWSTEEL", "JUBLFOOD", "KALYANKJIL", "KEI", "KOTAKBANK",
    "KPITTECH", "LAURUSLABS", "LENSKART", "LGEINDIA", "LICHSGFIN",
    "LODHA", "LT", "LTF", "LTM", "LUPIN",
    "M&M", "M&MFIN", "MANKIND", "MARICO", "MARUTI",
    "MAXHEALTH", "MAZDOCK", "MCX", "MFSL", "MOTHERSON",
    "MOTILALOFS", "MPHASIS", "MRF", "MUTHOOTFIN", "NATIONALUM",
    "NAUKRI", "NESTLEIND", "NHPC", "NMDC", "NTPC",
    "NYKAA", "OBEROIRLTY", "OFSS", "OIL", "ONGC",
    "PAGEIND", "PATANJALI", "PAYTM", "PERSISTENT", "PFC",
    "PHOENIXLTD", "PIDILITIND", "PIIND", "PNB", "POLICYBZR",
    "POLYCAB", "POWERGRID", "POWERINDIA", "PREMIERENE", "PRESTIGE",
    "RADICO", "RECLTD", "RELIANCE", "RVNL", "SAIL",
    "SBICARD", "SBILIFE", "SBIN", "SHREECEM", "SHRIRAMFIN",
    "SIEMENS", "SOLARINDS", "SRF", "SUNPHARMA", "SUPREMEIND",
    "SUZLON", "SWIGGY", "TATACAP", "TATACOMM", "TATACONSUM",
    "TATAELXSI", "TATAINVEST", "TATAPOWER", "TATASTEEL", "TCS",
    "TECHM", "TIINDIA", "TITAN", "TMCV", "TMPV",
    "TORNTPHARM", "TRENT", "TVSMOTOR", "ULTRACEMCO", "UNIONBANK",
    "UNITDSPR", "UPL", "VBL", "VEDL", "VMM",
    "VOLTAS", "WAAREEENER", "WIPRO", "YESBANK", "ZYDUSLIFE",
]
