"""后端语义检索的向量端点(Backend EMBEDDING_ENDPOINT 线约):
POST {"inputs": [...]} → {"embeddings": [[512 维], ...]};与检测共用同一个 bge-small-zh 模型。"""
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from core.semantic_encoder import get_shared_encoder

router = APIRouter(tags=['向量'])


class EmbedRequest(BaseModel):
    inputs: list[str] = Field(min_length=1, max_length=64)


def get_encoder(request: Request):
    return getattr(request.app.state, 'encoder', None) or get_shared_encoder(request.app.state.tender_service.config['embedding'])


@router.post('/embed')
def embed(payload: EmbedRequest, encoder: Annotated[object, Depends(get_encoder)]) -> dict:
    try:
        vectors = encoder.encode(payload.inputs)
    except (FileNotFoundError, ImportError, OSError) as exc:
        raise HTTPException(status_code=503, detail='本地语义模型不可用') from exc
    return {'embeddings': [[round(float(x), 6) for x in vector] for vector in vectors]}
