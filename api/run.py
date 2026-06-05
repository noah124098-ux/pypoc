if __name__ == "__main__":
    import os
    from pathlib import Path
    # Ensure working directory is repo root so .env and data/ are found by NSSM service
    repo_root = Path(__file__).resolve().parent.parent
    os.chdir(repo_root)
    try:
        from dotenv import load_dotenv
        load_dotenv(dotenv_path=repo_root / ".env", override=True)
    except ImportError:
        pass
    import uvicorn
    uvicorn.run("api.main:app", host="0.0.0.0", port=8502, reload=False)
