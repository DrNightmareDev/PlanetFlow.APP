from pydantic_settings import BaseSettings
from pydantic import Field
from functools import lru_cache


class Settings(BaseSettings):
    # Datenbank
    database_url: str = Field(default="postgresql://planetflow:password@localhost/planetflow", alias="DATABASE_URL")

    # EVE Online SSO
    eve_client_id: str = Field(default="", alias="EVE_CLIENT_ID")
    eve_client_secret: str = Field(default="", alias="EVE_CLIENT_SECRET")
    eve_callback_url: str = Field(default="http://localhost:8000/auth/callback", alias="EVE_CALLBACK_URL")
    eve_scopes: str = Field(
        default="esi-planets.manage_planets.v1,esi-planets.read_customs_offices.v1,esi-location.read_location.v1,esi-characters.read_corporation_roles.v1,esi-skills.read_skills.v1",
        alias="EVE_SCOPES"
    )

    # Markt API
    janice_api_key: str = Field(default="", alias="JANICE_API_KEY")

    # Sicherheit
    secret_key: str = Field(default="change-me-to-a-secure-random-key-32chars", alias="SECRET_KEY")

    # Server
    app_port: int = Field(default=8000, alias="APP_PORT")
    debug: bool = Field(default=False, alias="DEBUG")
    # Set to true only when the app is served over HTTPS.
    # Leave false (default) for plain HTTP installs — the browser will
    # otherwise silently drop the session cookie and every login redirects
    # back to the login page.
    cookie_secure: bool = Field(default=False, alias="COOKIE_SECURE")

    model_config = {"env_file": ".env", "extra": "ignore"}


@lru_cache()
def get_settings() -> Settings:
    return Settings()
