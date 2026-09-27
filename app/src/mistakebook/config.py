"""集中配置。所有可调项都来自环境变量，带可用默认值。"""

from __future__ import annotations

import pathlib

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql://mistakebook:mistakebook_dev_pw@db:5432/mistakebook"

    deepseek_api_key: str = ""
    # 默认值用全限定名：容器内 Docker DNS 不继承宿主机的 DNS 后缀，
    # 裸主机名 fq-dgx-01 在容器里解析不了。详见 .env.example 的说明。
    deepseek_base_url: str = "http://fq-dgx-01.local:8888/v1"
    deepseek_model: str = "deepseek-v4.1-flash"

    daily_review_minutes: int = 20
    upload_dir: str = "/app/data/uploads"
    book_dir: str = "/app/data/books"

    # 静态前端目录（开发时为本机路径，容器内为 /app/web）
    web_dir: str = "/app/web"

    @property
    def upload_path(self) -> pathlib.Path:
        return pathlib.Path(self.upload_dir)

    @property
    def book_path(self) -> pathlib.Path:
        return pathlib.Path(self.book_dir)

    @property
    def web_path(self) -> pathlib.Path:
        return pathlib.Path(self.web_dir)


settings = Settings()