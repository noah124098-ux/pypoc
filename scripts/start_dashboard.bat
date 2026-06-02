@echo off
nssm start pypoc-dashboard 2>nul || (
    cd /d C:\Users\Administrator\pypoc
    call .venv\Scripts\activate.bat
    python -m streamlit run dashboard.py --server.port 8501 --server.headless true --server.address 0.0.0.0
)
