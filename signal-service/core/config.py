"""服务配置入口。相对模型路径以配置文件目录为基准。"""
import os
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def load_config(path: str | Path | None = None) -> dict:
    path = Path(path) if path else ROOT / 'config.yaml'
    config = yaml.safe_load(path.read_text(encoding='utf-8'))
    if os.getenv('SIGNAL_MODEL_PATH'):
        config['embedding']['local_path'] = os.environ['SIGNAL_MODEL_PATH']
    model_path = Path(config['embedding']['local_path'])
    if not model_path.is_absolute():
        config['embedding']['local_path'] = str((path.resolve().parent / model_path).resolve())
    return config
