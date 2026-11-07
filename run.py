"""Start with one backend process; never use --workers > 1."""
import uvicorn

if __name__ == "__main__":
    uvicorn.run("analyzer.app:app", host="127.0.0.1", port=8000, workers=1, access_log=False)
