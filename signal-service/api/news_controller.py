"""接收采集端新闻，检测后只提交已获调查资格的信号。"""
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request

from signals.news.service import NewsDetectionRequest, NewsDetectionResponse, NewsService

router = APIRouter(prefix='/api/v1/signals/news', tags=['新闻信号'])


def get_news_service(request: Request) -> NewsService:
    return request.app.state.news_service


@router.post('/detect', response_model=NewsDetectionResponse)
def detect(payload: NewsDetectionRequest, service: Annotated[NewsService, Depends(get_news_service)]):
    try:
        return service.detect(payload.items, payload.company_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except (RuntimeError, OSError) as exc:
        raise HTTPException(status_code=503, detail='新闻目录正在使用或配置不可用，请检查服务状态') from exc
