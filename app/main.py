from __future__ import annotations  # 타입 힌트
from app.pipeline import run_pipeline  # 파이프라인 실행 함수


if __name__ == "__main__":  # 엔트리포인트
    result = run_pipeline()  # 실행
    print("")  # 보기 좋게 한 줄
    print("=== PIPELINE RESULT ===")  # 결과 헤더
    print("OK:", result.ok)  # 성공 여부
    print("Message:", result.message)  # 메시지
    print("Processed:", result.processed_count)  # 처리 수
    print("Post Path:", result.post_path)  # 포스트 경로
    print("Post Slug:", result.post_slug)  # slug
    if result.errors:  # 에러가 있으면
        print("Errors:", result.errors)  # 에러 출력
