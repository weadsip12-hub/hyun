import os.path
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials

# 1. 기본 설정 (기존 정보와 동일)
SCOPES = ['https://www.googleapis.com/auth/blogger']
BLOG_ID = "1354186921460852688"

# 🔍 여기에 삭제하고 싶은 글의 제목 키워드를 입력 (예: 카톡 파일명 일부)
TARGET_KEYWORD = "오늘" 

def run_cleaner():
    # token.json이 있어야 실행 가능
    if not os.path.exists('token.json'):
        print("❌ 인증 정보(token.json)가 없습니다. 먼저 blog.py를 실행해 인증해주세요.")
        return

    creds = Credentials.from_authorized_user_file('token.json', SCOPES)
    blogger = build('blogger', 'v3', credentials=creds)

    print(f"🔎 '{TARGET_KEYWORD}' 문구가 포함된 게시글을 찾는 중...")

    # 게시글 목록 가져오기 (최근 50개)
    try:
        posts_results = blogger.posts().list(blogId=BLOG_ID, maxResults=50).execute()
        posts = posts_results.get('items', [])

        if not posts:
            print("📭 삭제할 게시글이 없습니다.")
            return

        deleted_count = 0
        for post in posts:
            # 제목 비교 (키워드가 포함되어 있으면 삭제)
            if TARGET_KEYWORD in post['title']:
                print(f"🗑️ 삭제 중: {post['title']}")
                blogger.posts().delete(blogId=BLOG_ID, postId=post['id']).execute()
                deleted_count += 1
        
        print(f"\n✅ 작업 완료: 총 {deleted_count}개의 글을 삭제했습니다.")

    except Exception as e:
        print(f"❌ 에러 발생: {str(e)}")

if __name__ == '__main__':
    run_cleaner()