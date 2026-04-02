from pydantic_settings import BaseSettings
from typing import List
import os


class Settings(BaseSettings):
    # RTSP Streams
    RTSP1: str = ""
    RTSP2: str = ""

    # App
    APP_HOST: str = "0.0.0.0"
    APP_PORT: int = 8000
    SECRET_KEY: str = "changeme"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30

    # MongoDB
    # Khi chạy docker-compose với network_mode: host → dùng localhost:28017
    # Khi chạy bridge network → dùng mongodb://mongodb:28017
    MONGODB_URL: str = "mongodb://localhost:28017"
    MONGODB_DB_NAME: str = "face_recognition"
    MONGODB_PORT: int = 28017

    # InsightFace
    INSIGHTFACE_MODEL: str = "buffalo_l"
    FACE_THRESHOLD: float = 0.5
    FACE_IMAGES_DIR: str = "./face_images"

    class Config:
        env_file = ".env"
        extra = "allow"

    def get_rtsp_streams(self) -> List[str]:
        """Return all non-empty RTSP stream URLs from .env"""
        streams = []
        for key, val in self.__dict__.items():
            if key.upper().startswith("RTSP") and val:
                streams.append(val)
        return streams


settings = Settings()

# Ensure face images directory exists
os.makedirs(settings.FACE_IMAGES_DIR, exist_ok=True)
