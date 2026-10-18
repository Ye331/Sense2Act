"""应用启动入口：装配配置、服务和控制器。"""
import os

from fastapi import FastAPI

from core.config import load_config
from api.tender_controller import router
from api.news_controller import router as news_router
from api.embedding_controller import router as embedding_router
from signals.news.service import NewsService
from signals.tender.service import TenderService


def create_app(service: TenderService | None = None, news_service: NewsService | None = None) -> FastAPI:
    app = FastAPI(title="企业信号发现", version="0.2.0")
    app.state.tender_service = service if service is not None else TenderService(
        load_config(os.getenv("SIGNAL_CONFIG"))
    )
    app.state.news_service = news_service if news_service is not None else NewsService(
        task_client=app.state.tender_service.task_client)
    app.include_router(router)
    app.include_router(news_router)
    app.include_router(embedding_router)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
