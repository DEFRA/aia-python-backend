from app.api.main import app

# This is a shim to support legacy uvicorn app.main:app commands
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8086)
