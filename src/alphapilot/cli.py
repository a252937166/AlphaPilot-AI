def run_api() -> None:
    import uvicorn

    uvicorn.run("alphapilot.main:app", host="127.0.0.1", port=8000, reload=False)
