def run_api() -> None:
    import uvicorn

    uvicorn.run("alphapilot.main:app", host="0.0.0.0", port=8000, reload=False)
