"""招投标检测控制器：接收请求，委托业务服务。"""
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request

from signals.tender.schemas import DetectionRequest, DetectionResponse
from signals.tender.service import TenderService

router = APIRouter(prefix="/api/v1/signals/tender", tags=["招投标信号"])


def get_tender_service(request: Request) -> TenderService:
    return request.app.state.tender_service


@router.post("/detect", response_model=DetectionResponse)
def detect(
    payload: DetectionRequest,
    service: Annotated[TenderService, Depends(get_tender_service)],
) -> DetectionResponse:
    try:
        return service.detect(payload)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except (FileNotFoundError, ImportError, OSError) as exc:
        raise HTTPException(
            status_code=503,
            detail="本地语义模型或运行依赖不可用，请检查服务配置",
        ) from exc
