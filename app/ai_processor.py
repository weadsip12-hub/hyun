from __future__ import annotations  # 타입 힌트 안정화
import json  # 더미 캡션/본문 만들 때 사용
import os  # 환경변수 읽기(실모드에서만 필요)
from dataclasses import dataclass  # 클래스 간단 정의
from pathlib import Path  # 경로 처리
from typing import Any, Dict, List  # 타입 힌트

from app.drive_manager import DriveImage  # DriveImage 재사용


@dataclass
class AIProcessor:  # AI 처리(실제 호출 or 더미 생성) 담당
    provider: str  # "gemini" 등
    model: str  # 모델명
    api_key: str | None  # ✅ 더미모드면 None 가능
    prompts_dir: Path  # prompts 폴더 경로
    mock_mode: bool = False  # ✅ 더미모드 플래그

    def _read_prompt(self, filename: str) -> str:  # 프롬프트 파일 읽기
        path = self.prompts_dir / filename  # prompts/filename 경로
        if not path.exists():  # 파일 없으면
            raise FileNotFoundError(f"Missing prompt file: {path}")  # 에러
        return path.read_text(encoding="utf-8")  # 텍스트 반환

    # -----------------------------
    # ✅ 더미 생성 로직 (결제/쿼터 없어도 계속 개발 가능)
    # -----------------------------
    def _mock_captions(self, images: List[DriveImage]) -> Dict[str, Any]:  # 더미 캡션 JSON 생성
        return {  # 요구 스키마 유지
            "images": [
                {
                    "index": idx,  # 1부터 시작
                    "line1": f"(더미) 사진 {idx} 한 줄 소개",  # 첫 줄
                    "line2": f"(더미) 사진 {idx} 두 번째 줄 소개",  # 둘째 줄
                }
                for idx in range(1, len(images) + 1)  # 이미지 개수만큼 생성
            ]
        }

    def _mock_post(self, captions_json: Dict[str, Any]) -> str:  # 더미 본문 생성(마크다운/텍스트)
        lines = []  # 출력 라인 모음
        lines.append("더미 제목: 자동 블로그 포스팅 테스트")  # 제목(plain text)
        lines.append("")  # 빈 줄
        lines.append("오늘은 자동화 파이프라인을 더미모드로 테스트했어.")  # 1문단
        lines.append("")  # 빈 줄
        lines.append("사진별 요약:")  # 섹션 라벨
        for item in captions_json.get("images", []):  # 캡션 리스트 순회
            lines.append(f"- 사진 {item['index']}: {item['line1']} / {item['line2']}")  # 간단 요약
        lines.append("")  # 빈 줄
        lines.append("마무리: AI 연결되면 여기 내용이 실제 글로 바뀔 거야.")  # 마무리
        return "\n".join(lines)  # 문자열로 합쳐 반환

    # -----------------------------
    # ✅ 외부에서 쓰는 메인 함수 2개
    # -----------------------------
    def generate_photo_captions(self, images: List[DriveImage]) -> Dict[str, Any]:  # 이미지 -> 캡션 JSON
        if not images:  # 이미지 없으면
            raise ValueError("generate_photo_captions: images is empty")  # 에러
        images = images[:4]  # 최대 4장 제한(요구사항)

        if self.mock_mode:  # ✅ 더미모드면
            return self._mock_captions(images)  # 더미 JSON 반환

        # ✅ 실모드(나중에 결제 후 구현/복구할 영역)
        raise RuntimeError("Real AI mode is disabled for now. Set ai.mock_mode=true to continue development.")  # 안내

    def generate_post_markdown(self, captions_json: Dict[str, Any]) -> str:  # 캡션 JSON -> 본문 텍스트
        if self.mock_mode:  # ✅ 더미모드면
            return self._mock_post(captions_json)  # 더미 본문 반환

        # ✅ 실모드(나중에 결제 후 구현/복구할 영역)
        raise RuntimeError("Real AI mode is disabled for now. Set ai.mock_mode=true to continue development.")  # 안내


def create_ai_processor(config: Dict[str, Any]) -> AIProcessor:  # config로 AIProcessor 생성
    ai_cfg = config.get("ai", {})  # ai 섹션
    provider = ai_cfg.get("provider", "gemini")  # 기본값
    model = ai_cfg.get("model", "gemini-2.0-flash")  # 기본값
    mock_mode = bool(ai_cfg.get("mock_mode", False))  # ✅ 더미모드 여부

    base_dir = Path(__file__).resolve().parent.parent  # 프로젝트 루트
    prompts_dir = base_dir / "prompts"  # prompts 폴더

    api_key = None  # ✅ 더미모드면 키 없어도 됨
    if not mock_mode:  # 실모드일 때만 키 요구
        api_key = os.getenv("GEMINI_API_KEY") or os.getenv("AI_API_KEY")  # 환경변수에서 읽기
        if not api_key:  # 키 없으면
            raise ValueError("Missing API key: set GEMINI_API_KEY (or set ai.mock_mode=true)")  # 안내

    return AIProcessor(  # 객체 생성
        provider=provider,  # provider 저장
        model=model,  # model 저장
        api_key=api_key,  # 키(더미모드면 None)
        prompts_dir=prompts_dir,  # prompts 경로
        mock_mode=mock_mode,  # 더미모드 플래그
    )


if __name__ == "__main__":  # 단독 테스트(더미 캡션+더미 본문 출력)
    from app.config_loader import load_config  # 설정 로드
    from app.state_client import create_state_client, _build_drive_service  # Drive 인증
    from app.drive_manager import create_drive_manager  # 신규 이미지 다운로드

    cfg = load_config()  # config 로드
    service = _build_drive_service()  # Drive service 생성
    state = create_state_client(cfg)  # state client 생성
    dm = create_drive_manager(cfg, service)  # drive manager 생성

    new_imgs = dm.pick_new_images(state)  # 신규 이미지 선택
    if not new_imgs:  # 신규 없으면
        print("No new images found.")  # 안내
        raise SystemExit(0)  # 종료

    downloaded = dm.download_images(new_imgs, subdir="incoming")  # 로컬 다운로드
    ai = create_ai_processor(cfg)  # AIProcessor 생성(더미모드)

    captions = ai.generate_photo_captions(downloaded)  # ✅ 더미 캡션 생성
    print("CAPTIONS_JSON:")  # 라벨
    print(json.dumps(captions, ensure_ascii=False, indent=2))  # 출력

    post = ai.generate_post_markdown(captions)  # ✅ 더미 본문 생성
    print("\nPOST_TEXT:")  # 라벨
    print(post)  # 출력
