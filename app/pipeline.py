from __future__ import annotations  # 타입 힌트 안정화
import subprocess  # git 안전 점검용
import traceback  # 에러 스택 출력용
from dataclasses import dataclass  # 결과 구조화
from datetime import datetime  # 타임스탬프 로그
from typing import Any, Dict, List, Optional  # 타입 힌트

from app.config_loader import load_config  # 설정 로더
from app.state_client import create_state_client, _build_drive_service  # Drive 인증 + state
from app.drive_manager import create_drive_manager, DriveImage  # Drive 이미지 관리
from app.ai_processor import create_ai_processor  # AI(더미/실제)
from app.content_builder import create_content_builder, BuildResult  # 콘텐츠 생성
from app.git_publisher import create_git_publisher  # git publish


@dataclass
class PipelineResult:  # 파이프라인 실행 결과 요약
    ok: bool  # 성공 여부
    message: str  # 요약 메시지
    processed_count: int = 0  # 처리한 이미지 수
    post_path: Optional[str] = None  # 생성된 포스트 경로
    post_slug: Optional[str] = None  # 생성된 slug
    errors: Optional[List[str]] = None  # 에러 목록(있으면)


class Pipeline:  # 전체 자동화 엔진(실무급)
    def __init__(self, config: Dict[str, Any]) -> None:  # 생성자
        self.config = config  # 설정 저장
        self._drive_service = _build_drive_service()  # Drive API 서비스 생성(이미 세팅됨)
        self.state_client = create_state_client(config)  # state client 생성(Drive state.json)
        self.drive_manager = create_drive_manager(config, self._drive_service)  # drive manager 생성
        self.ai = create_ai_processor(config)  # AI 프로세서 생성(더미모드 포함)
        self.builder = create_content_builder(config)  # content builder 생성
        self.git = create_git_publisher(config)  # git publisher 생성

    # -----------------------------
    # 로깅(최소지만 실무에서 읽기 좋게)
    # -----------------------------
    def _log(self, level: str, msg: str) -> None:  # 콘솔 로그 출력
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")  # 타임스탬프
        print(f"[{ts}] [{level}] {msg}")  # 표준 로그 포맷

    # -----------------------------
    # Git 안전 점검 (비밀 파일/토큰이 추적되면 즉시 중단)
    # -----------------------------
    def _git_is_tracked(self, rel_path: str) -> bool:  # 특정 파일이 git에 tracked인지 확인
        try:
            subprocess.run(  # git ls-files로 추적 여부 확인
                ["git", "ls-files", "--error-unmatch", rel_path],  # tracked면 0, 아니면 non-zero
                cwd=str(self.git.repo_dir),  # repo 루트
                capture_output=True,  # 출력 캡처
                text=True,  # 텍스트 모드
                encoding="utf-8",  # 인코딩
                check=True,  # 실패 시 예외
            )
            return True  # 여기까지 오면 tracked
        except Exception:
            return False  # 예외면 tracked 아님

    def _preflight_security_checks(self) -> None:  # 보안/안전 사전 점검
        # ✅ 비밀 파일들이 절대 tracked면 안 됨
        secrets = ["client_secret.json", "token.json", ".env"]  # 최소 필수 시크릿
        for s in secrets:  # 시크릿 반복
            if self._git_is_tracked(s):  # tracked면
                raise RuntimeError(f"SECURITY BLOCK: '{s}' is tracked by git. Remove it from git history and add to .gitignore.")  # 즉시 중단

    # -----------------------------
    # 파이프라인 단계별 실행
    # -----------------------------
    def _pick_and_download(self) -> List[DriveImage]:  # 신규 이미지 선택 + 다운로드
        self._log("INFO", "Scanning Google Drive for new images...")  # 로그
        new_images = self.drive_manager.pick_new_images(self.state_client)  # state 기반 신규 선택
        if not new_images:  # 없으면
            self._log("INFO", "No new images found. Nothing to do.")  # 로그
            return []  # 빈 리스트 반환

        self._log("INFO", f"Found {len(new_images)} new image(s). Downloading...")  # 로그
        downloaded = self.drive_manager.download_images(new_images, subdir="incoming")  # incoming에 다운로드
        for img in downloaded:  # 확인용 로그
            self._log("INFO", f"Downloaded: {img.name} -> {img.local_path}")  # 로그
        return downloaded  # 다운로드 결과 반환

    def _ai_generate(self, downloaded: List[DriveImage]) -> tuple[Dict[str, Any], str]:  # AI 캡션 + 본문 생성
        self._log("INFO", "Generating captions (1 call for up to 4 images)...")  # 로그
        captions = self.ai.generate_photo_captions(downloaded)  # 캡션 JSON 생성(더미/실제)
        self._log("INFO", "Generating post text (1 call)...")  # 로그
        post_text = self.ai.generate_post_markdown(captions)  # 본문 생성(더미/실제)
        return captions, post_text  # 결과 반환

    def _build_content(self, captions: Dict[str, Any], post_text: str, downloaded: List[DriveImage]) -> BuildResult:  # md + 이미지 정리 생성
        self._log("INFO", "Building blog content (markdown + images)...")  # 로그
        result = self.builder.build(captions, post_text, downloaded)  # 파일 생성
        self._log("INFO", f"Post created: {result.post_path}")  # 로그
        return result  # 결과 반환

    def _git_publish(self, build_result: BuildResult) -> None:  # git add/commit/push
        # ✅ 커밋 메시지는 config에서 템플릿으로 조정 가능
        git_cfg = self.config.get("git", {})  # git 섹션
        template = git_cfg.get("commit_message_template", "chore: publish {slug}")  # 기본 템플릿
        msg = template.format(slug=build_result.post_slug)  # slug로 치환
        self._log("INFO", f"Publishing to GitHub (branch={self.git.branch})...")  # 로그
        self.git.publish(msg)  # add+commit+push
        self._log("INFO", "GitHub publish done.")  # 로그

    def _update_state(self, downloaded: List[DriveImage], slug: str) -> int:  # state.json 업데이트(최종 단계)
        self._log("INFO", "Updating state.json on Google Drive (mark processed)...")  # 로그
        ok_count = 0  # 성공 카운트
        for img in downloaded:  # 이미지별 기록
            try:
                self.state_client.mark_processed(img.file_id, slug)  # 성공 후에만 처리 표시
                ok_count += 1  # 카운트 증가
            except Exception as e:
                self._log("ERROR", f"Failed to mark processed for {img.file_id}: {e}")  # 실패 로그(파이프라인 전체는 성공 처리해도 됨)
        self._log("INFO", f"State updated for {ok_count}/{len(downloaded)} image(s).")  # 로그
        return ok_count  # 기록 성공 수 반환

    # -----------------------------
    # 메인 실행
    # -----------------------------
    def run(self) -> PipelineResult:  # 전체 파이프라인 실행(실무급 순서/보장)
        errors: List[str] = []  # 에러 수집

        try:
            self._preflight_security_checks()  # ✅ 보안 점검(시크릿 추적 차단)
        except Exception as e:
            return PipelineResult(ok=False, message=str(e), errors=[str(e)])  # 보안 실패면 즉시 종료

        downloaded: List[DriveImage] = []  # 다운로드 결과(실패 시 상태값 유지)
        build_result: Optional[BuildResult] = None  # 콘텐츠 결과(실패 시 None)

        try:
            downloaded = self._pick_and_download()  # 1) 신규 선택 + 다운로드
            if not downloaded:  # 신규 없으면
                return PipelineResult(ok=True, message="No new images.", processed_count=0)  # 정상 종료

            captions, post_text = self._ai_generate(downloaded)  # 2) AI 생성(더미/실제)
            build_result = self._build_content(captions, post_text, downloaded)  # 3) 콘텐츠 생성

            # ✅ 여기까지는 로컬 결과물 생성 단계 (실패해도 state 업데이트 X)
            self._git_publish(build_result)  # 4) GitHub publish (실패 가능)

            # ✅ push 성공 이후에만 state 기록 (가장 중요)
            marked = self._update_state(downloaded, build_result.post_slug)  # 5) state 업데이트
            return PipelineResult(  # 성공 결과 반환
                ok=True,
                message="Pipeline completed successfully.",
                processed_count=marked,
                post_path=build_result.post_path,
                post_slug=build_result.post_slug,
                errors=None,
            )

        except Exception as e:
            # ✅ 실무급: 에러는 요약 + 스택은 출력(디버깅용)
            err_msg = f"{type(e).__name__}: {e}"  # 에러 요약
            errors.append(err_msg)  # 리스트에 추가
            self._log("ERROR", err_msg)  # 로그 출력
            self._log("ERROR", traceback.format_exc())  # 스택 출력

            # ✅ 실패 시 보장: state 업데이트는 절대 하지 않음(위에서 publish 후에만 update_state 호출)
            # ✅ 로컬 결과물(post 파일)은 남을 수 있음(문제 없음). 다음 실행 시 중복 생성 가능 → 추후 개선 가능.

            return PipelineResult(  # 실패 결과 반환
                ok=False,
                message="Pipeline failed.",
                processed_count=0,
                post_path=(build_result.post_path if build_result else None),
                post_slug=(build_result.post_slug if build_result else None),
                errors=errors,
            )


def run_pipeline() -> PipelineResult:  # 외부에서 한 줄로 실행할 수 있게 래퍼
    cfg = load_config()  # config 로드
    p = Pipeline(cfg)  # 파이프라인 생성
    return p.run()  # 실행 결과 반환
