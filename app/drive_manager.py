from __future__ import annotations  # 타입 힌트 안정화
import os  # 폴더 생성/경로 처리를 위해 사용
from dataclasses import dataclass  # 간단한 데이터 구조 정의
from pathlib import Path  # OS 독립 경로 처리
from typing import Any, Dict, List, Optional  # 타입 힌트

from googleapiclient.http import MediaIoBaseDownload  # Drive 파일 다운로드
from io import BytesIO  # 메모리 버퍼 다운로드용

from app.state_client import StateClient  # state 확인용


IMAGE_MIME_PREFIX = "image/"  # 이미지 MIME 타입 prefix


@dataclass
class DriveImage:  # Drive에서 가져온 이미지 파일 정보(필요 최소)
    file_id: str  # Drive 파일 ID
    name: str  # 파일명
    mime_type: str  # MIME 타입
    modified_time: str  # 수정 시간(정렬 등에 사용 가능)
    local_path: Optional[str] = None  # 다운로드 후 로컬 저장 경로


@dataclass
class DriveManager:  # Drive 폴더에서 신규 이미지를 찾고 다운로드하는 클래스
    drive_service: Any  # google drive service 객체
    input_folder_id: str  # 사진 업로드 폴더 ID
    images_root: Path  # 로컬 이미지 저장 루트 경로 (예: blog/assets/images)
    batch_size: int = 4  # 한 번에 처리할 최대 사진 수

    def _list_images_in_folder(self) -> List[DriveImage]:  # 폴더 내 이미지 파일 목록 조회
        q = (  # Drive 검색 쿼리
            f"'{self.input_folder_id}' in parents and "  # 특정 폴더 안에서
            "trashed = false and "  # 휴지통 제외
            f"mimeType contains '{IMAGE_MIME_PREFIX}'"  # image/* 만
        )
        resp = self.drive_service.files().list(  # 파일 목록 조회 호출
            q=q,  # 검색 조건
            fields="files(id,name,mimeType,modifiedTime)",  # 필요한 필드만 요청(속도/권한 최소화)
            pageSize=200  # 적당히 크게(폴더가 커지면 페이지네이션 추가 가능)
        ).execute()  # 실행
        files = resp.get("files", [])  # 결과 리스트
        images: List[DriveImage] = []  # 반환용 리스트
        for f in files:  # 파일 순회
            images.append(  # DriveImage로 변환
                DriveImage(
                    file_id=f["id"],  # 파일 ID
                    name=f["name"],  # 파일명
                    mime_type=f.get("mimeType", ""),  # MIME 타입
                    modified_time=f.get("modifiedTime", ""),  # 수정 시간
                )
            )
        images.sort(key=lambda x: x.modified_time, reverse=False)  # 오래된 것부터 정렬(원하면 최신부터로 바꿔도 됨)
        return images  # 이미지 목록 반환

    def pick_new_images(self, state_client: StateClient) -> List[DriveImage]:  # 신규 이미지만 뽑아서 batch_size개 반환
        all_images = self._list_images_in_folder()  # 폴더의 전체 이미지 목록
        new_images: List[DriveImage] = []  # 신규만 담을 리스트
        for img in all_images:  # 전체 중에서
            if not state_client.is_processed(img.file_id):  # state에 없으면(미처리)
                new_images.append(img)  # 신규로 추가
            if len(new_images) >= self.batch_size:  # batch_size만큼 모이면
                break  # 중단
        return new_images  # 신규 이미지 리스트 반환

    def _safe_filename(self, name: str) -> str:  # 윈도우에서 문제될 수 있는 문자 제거(아주 최소)
        bad = ['<', '>', ':', '"', '/', '\\', '|', '?', '*']  # 윈도우 금지 문자
        for ch in bad:  # 금지 문자 순회
            name = name.replace(ch, "_")  # 밑줄로 치환
        return name  # 정리된 파일명 반환

    def download_images(self, images: List[DriveImage], subdir: str) -> List[DriveImage]:  # 이미지들을 로컬로 다운로드
        target_dir = self.images_root / subdir  # 저장 폴더 (예: blog/assets/images/2026-01-23)
        target_dir.mkdir(parents=True, exist_ok=True)  # 폴더 없으면 생성

        downloaded: List[DriveImage] = []  # 다운로드 완료 리스트
        for img in images:  # 이미지별 반복
            safe_name = self._safe_filename(img.name)  # 파일명 정리
            local_path = target_dir / safe_name  # 로컬 저장 경로 만들기

            request = self.drive_service.files().get_media(fileId=img.file_id)  # 다운로드 요청
            fh = BytesIO()  # 메모리 버퍼 생성
            downloader = MediaIoBaseDownload(fh, request)  # 다운로드 객체 생성
            done = False  # 완료 플래그
            while not done:  # 완료까지 반복
                _, done = downloader.next_chunk()  # 다음 청크 다운로드

            with open(local_path, "wb") as f:  # 로컬 파일로 저장
                f.write(fh.getvalue())  # 버퍼 내용을 파일로 씀

            img.local_path = str(local_path)  # DriveImage에 로컬 경로 기록
            downloaded.append(img)  # 완료 리스트에 추가

        return downloaded  # 다운로드된 이미지 리스트 반환


def create_drive_manager(config: Dict[str, Any], drive_service: Any) -> DriveManager:  # config로 DriveManager 생성
    drive_cfg = config.get("drive", {})  # drive 섹션
    blog_cfg = config.get("blog", {})  # blog 섹션
    pipeline_cfg = config.get("pipeline", {})  # pipeline 섹션

    input_folder_id = drive_cfg.get("input_folder_id")  # 입력 사진 폴더 ID
    if not input_folder_id:  # 없으면
        raise ValueError("config.drive.input_folder_id is required")  # 명확히 에러

    images_path = blog_cfg.get("images_path", "blog/assets/images")  # 이미지 저장 루트 경로
    batch_size = int(pipeline_cfg.get("batch_size", 4))  # 배치 크기(기본 4)

    base_dir = Path(__file__).resolve().parent.parent  # 프로젝트 루트 경로
    images_root = base_dir / images_path  # 실제 로컬 이미지 루트 경로

    return DriveManager(  # DriveManager 객체 생성
        drive_service=drive_service,  # Drive service 주입
        input_folder_id=input_folder_id,  # Drive 폴더 ID
        images_root=images_root,  # 로컬 저장 루트
        batch_size=batch_size,  # 배치 크기
    )


if __name__ == "__main__":  # 단독 실행 테스트(신규 1~4장 다운로드까지)
    from app.config_loader import load_config  # config 읽기
    from app.state_client import create_state_client, _build_drive_service  # state client / drive service 생성

    cfg = load_config()  # config 로드
    service = _build_drive_service()  # Drive service 생성(토큰/서비스계정)
    state = create_state_client(cfg)  # state client 생성
    mgr = create_drive_manager(cfg, service)  # drive manager 생성

    new_imgs = mgr.pick_new_images(state)  # 신규 이미지 선택
    print(f"New images: {len(new_imgs)}")  # 몇 개 뽑혔는지 출력
    for i in new_imgs:  # 파일 정보 출력
        print(i.file_id, i.name, i.modified_time)  # 간단히 출력

    if new_imgs:  # 신규가 있으면
        downloaded = mgr.download_images(new_imgs, subdir="incoming")  # blog/assets/images/incoming 아래 다운로드
        print("Downloaded:")  # 다운로드 완료 출력
        for d in downloaded:  # 다운로드 결과 출력
            print(d.local_path)  # 로컬 저장 경로 출력
