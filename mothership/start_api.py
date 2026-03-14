"""Start the Mothership FastAPI server."""
import uvicorn

if __name__ == "__main__":
    uvicorn.run("api.server:app", host="0.0.0.0", port=3000, reload=False)
