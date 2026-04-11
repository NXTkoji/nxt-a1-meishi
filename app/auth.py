from fastapi import Header, HTTPException

from app.config import settings


async def verify_api_key(authorization: str = Header(default="")) -> None:
    # If no api_key is configured, the app is running locally — skip auth.
    if not settings.api_key:
        return
    if authorization != f"Bearer {settings.api_key}":
        raise HTTPException(status_code=401, detail="Invalid API key")
