# modules/config_loader.py
# 목적: ~/.hermes/config.yaml 을 싱글턴으로 로드

import os
import yaml

_config = None


def load_config(path: str = "~/.hermes/config.yaml") -> dict:
    """
    config.yaml 을 읽어 dict 반환 (싱글턴 캐시).
    환경변수 HERMES_CONFIG_PATH 로 경로 오버라이드 가능.
    """
    global _config
    if _config is None:
        resolved = os.path.expanduser(
            os.getenv("HERMES_CONFIG_PATH", path)
        )
        if not os.path.exists(resolved):
            raise FileNotFoundError(
                f"Hermes 설정 파일을 찾을 수 없습니다: {resolved}\n"
                "~/.hermes/config.yaml 을 먼저 생성하세요."
            )
        with open(resolved, "r", encoding="utf-8") as f:
            _config = yaml.safe_load(f)
    return _config


def reload_config(path: str = "~/.hermes/config.yaml") -> dict:
    """캐시 무효화 후 재로드"""
    global _config
    _config = None
    return load_config(path)
