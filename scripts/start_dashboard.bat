@echo off
cd /d C:\Users\Administrator\pypoc
call .venv\Scripts\activate.bat
echo Starting Dashboard on port 8501...
python -m streamlit run dashboard.py --server.port 8501 --server.headless true --server.address 0.0.0.0 >> logs\dashboard.log 2>&1
