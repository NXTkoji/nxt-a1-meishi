from pathlib import Path
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # API auth
    api_key: str = ""

    # Anthropic
    anthropic_api_key: str = ""
    claude_model: str = "claude-sonnet-4-6"

    # Odoo (nxta.co account — primary)
    odoo_url: str = ""
    odoo_db: str = ""
    odoo_username: str = ""
    odoo_password: str = ""

    # Google People API
    google_client_id: str = ""
    google_client_secret: str = ""
    google_refresh_token: str = ""

    # Google Drive (image backup)
    google_drive_folder_id: str = ""
    google_drive_images_folder_id: str = ""

    # Microsoft Graph (OneDrive)
    ms_client_id: str = ""
    ms_client_secret: str = ""
    ms_refresh_token: str = ""
    ms_tenant_id: str = ""
    onedrive_folder: str = "BusinessCards"

    # Local storage — default to ~/.nxt-a1/
    data_dir: Path = Path.home() / ".nxt-a1"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "meishi.db"

    @property
    def database_url(self) -> str:
        return f"sqlite+aiosqlite:///{self.db_path}"

    @property
    def images_path(self) -> Path:
        return self.data_dir / "images"

    @property
    def temp_path(self) -> Path:
        return self.data_dir / "temp"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "env_ignore_empty": True}


settings = Settings()
