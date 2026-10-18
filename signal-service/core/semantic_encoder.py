"""本地语义模型加载与向量缓存。"""
from functools import lru_cache
from pathlib import Path
from threading import RLock

import numpy as np

from core.config import ROOT


@lru_cache(maxsize=2)
def _load_model(local_path: str, cpu_threads: int, revision: str = '', model_signature: tuple = ()):
    """版本和文件签名参与缓存键，模型变更后自动重新加载。"""
    from sentence_transformers import SentenceTransformer
    import torch

    if not Path(local_path, 'model.safetensors').is_file():
        raise FileNotFoundError(f'缺少本地语义模型：{local_path}。请安装配置中的模型；不会回退到关键词或 TF-IDF。')
    torch.set_num_threads(cpu_threads)
    return SentenceTransformer(local_path, device='cpu', local_files_only=True, trust_remote_code=False)


class SemanticEncoder:
    """使用固定预训练模型，仅缓存文本向量，不在回放数据上训练。"""
    def __init__(self, config: dict):
        settings = config['embedding']
        root = ROOT
        self.model = _load_model(str((root / settings['local_path']).resolve()), int(settings['cpu_threads']),
                                 settings.get('revision', ''), _model_signature(settings))
        self.batch_size = int(settings['batch_size'])
        self.cache: dict[str, np.ndarray] = {}
        self.lock = RLock()

    def encode(self, texts: list[str]) -> np.ndarray:
        with self.lock:
            return self._encode(texts)

    def _encode(self, texts: list[str]) -> np.ndarray:
        missing = list(dict.fromkeys(text for text in texts if text not in self.cache))
        if missing:
            vectors = self.model.encode(missing, batch_size=self.batch_size, normalize_embeddings=True,
                                        convert_to_numpy=True, show_progress_bar=False)
            self.cache.update(zip(missing, vectors))
        return np.asarray([self.cache[text] for text in texts])


def _model_signature(settings) -> tuple:
    directory = ROOT / settings['local_path']
    return tuple((str(path.relative_to(directory)), path.stat().st_size, path.stat().st_mtime_ns)
                 for path in sorted(directory.rglob('*')) if path.is_file())


@lru_cache(maxsize=2)
def _shared_encoder(settings_key: tuple, model_signature: tuple) -> SemanticEncoder:
    """同一模型配置共享编码器及向量缓存，不同模型版本相互隔离。"""
    return SemanticEncoder({'embedding': dict(settings_key)})


def get_shared_encoder(settings: dict) -> SemanticEncoder:
    return _shared_encoder(tuple(sorted(settings.items())), _model_signature(settings))
