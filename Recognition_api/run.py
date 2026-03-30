import uvicorn
from app.config import settings

if __name__ == "__main__":
    print(f"[INFO] Starting HTTP on port {settings.APP_PORT}")
    print(f"[INFO] UI: http://0.0.0.0:{settings.APP_PORT}")
    uvicorn.run(
        "app.main:app",
        host=settings.APP_HOST,
        port=settings.APP_PORT,
        reload=True,
    )
