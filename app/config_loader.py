from __future__ import annotations  # (선택) 타입힌트 호환성 안정화
from pathlib import Path  # 경로를 OS 독립적으로 다루기 위한 모듈
from typing import Any, Dict  # 타입힌트용
import yaml  # YAML 파싱 모듈 (pip install pyyaml 필요)


def _read_yaml(path: Path) -> Dict[str, Any]:  # YAML 파일을 dict로 읽는 내부 함수
    if not path.exists():  # 파일이 없으면
        return {}  # 선택 파일(paths.yaml 등)은 없어도 되게 빈 dict 반환
    with path.open("r", encoding="utf-8") as f:  # UTF-8로 파일 열기
        data = yaml.safe_load(f)  # YAML -> Python 객체(dict 등)로 변환
    if data is None:  # 파일이 비어있거나 null이면
        return {}  # 빈 dict로 통일
    if not isinstance(data, dict):  # 최상위가 dict가 아니면(예: 리스트 등)
        raise ValueError(f"YAML root must be a mapping(dict): {path}")  # 무엇이 문제인지 명확히 에러
    return data  # 정상 dict 반환


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:  # dict를 재귀적으로 병합
    result = dict(base)  # base 얕은 복사(원본 보호)
    for k, v in override.items():  # override의 모든 키를 순회
        if k in result and isinstance(result[k], dict) and isinstance(v, dict):  # 둘 다 dict면
            result[k] = _deep_merge(result[k], v)  # 재귀 병합
        else:  # 그 외에는
            result[k] = v  # override 값으로 덮어쓰기
    return result  # 병합 결과 반환


def load_config() -> Dict[str, Any]:  # config를 읽어 최종 설정 dict를 반환하는 메인 함수
    base_dir = Path(__file__).resolve().parent.parent  # blog-pipeline 루트 경로 계산
    config_dir = base_dir / "config"  # config 폴더 경로
    config_yaml = config_dir / "config.yaml"  # 필수 설정 파일 경로
    paths_yaml = config_dir / "paths.yaml"  # 선택 경로 설정 파일 경로

    if not config_yaml.exists():  # 필수 파일이 없으면
        raise FileNotFoundError(f"Missing required file: {config_yaml}")  # 바로 알 수 있게 에러

    cfg_main = _read_yaml(config_yaml)  # config.yaml 읽기
    cfg_paths = _read_yaml(paths_yaml)  # paths.yaml 읽기(없으면 {})

    cfg = _deep_merge(cfg_main, {"paths": cfg_paths} if cfg_paths else {})  # paths.yaml은 cfg["paths"] 아래로 넣기

    cfg.setdefault("project", {})  # project 섹션이 없으면 생성
    cfg["project"].setdefault("timezone", "Asia/Seoul")  # 기본 타임존은 Asia/Seoul

    cfg.setdefault("pipeline", {})  # pipeline 섹션이 없으면 생성
    cfg["pipeline"].setdefault("batch_size", 4)  # 요구사항 기본값: 4장

    if not isinstance(cfg["pipeline"].get("batch_size"), int):  # batch_size가 정수가 아니면
        raise ValueError("config.pipeline.batch_size must be an integer")  # 명확히 에러

    if cfg["pipeline"]["batch_size"] < 1 or cfg["pipeline"]["batch_size"] > 4:  # 1~4 범위를 벗어나면
        raise ValueError("config.pipeline.batch_size must be between 1 and 4")  # 요구사항 범위 강제

    return cfg  # 최종 config 반환


if __name__ == "__main__":  # 이 파일을 단독 실행할 때만 아래 실행
    config = load_config()  # config 로드
    print(config)  # 로드 결과 출력(테스트용)
