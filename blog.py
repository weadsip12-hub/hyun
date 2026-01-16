import os
import io
import json
import time
import subprocess
import re
import html
from pathlib import Path
from typing import Optional, Dict, List, Tuple

from google import genai
from google.genai import types

from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials

# =========================
# 🔧 1. 사용자 설정
# =========================
REAL_FOLDER_ID = "1v5VE_BRLNUlkEk_nXHSQHdMN4TkjoUiT"  # 구글 드라이브 폴더 ID
MODEL_NAME = "gemini-2.0-flash"                       # 사용할 모델
MAX_PHOTOS_PER_POST = 5                               # 한 포스트당 사진 개수
OUT_DIR = r"C:\Users\user\Desktop\blogtest"           # GitHub 레포 로컬 경로
STATE_FILE = os.path.join(OUT_DIR, "state.json")      # 중복 처리 방지 기록 파일

SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]

# OAuth 토큰 / 클라이언트 시크릿을 스크립트 위치 기준으로 관리 (작업 폴더 바뀌어도 안정)
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
TOKEN_PATH = os.path.join(SCRIPT_DIR, "token_drive.json")
CLIENT_SECRET_PATH = os.path.join(SCRIPT_DIR, "client_secret.json")


# =========================
# 🛠️ 2. 환경 변수 및 인증
# =========================
def read_win_env(name: str) -> Optional[str]:
    """Windows 환경변수(User/Machine) 읽기"""
    try:
        v = subprocess.check_output(
            ["powershell", "-NoProfile", "-Command",
             f"[System.Environment]::GetEnvironmentVariable('{name}','Machine')"],
            text=True
        ).strip()
        if v:
            return v

        v = subprocess.check_output(
            ["powershell", "-NoProfile", "-Command",
             f"[System.Environment]::GetEnvironmentVariable('{name}','User')"],
            text=True
        ).strip()
        return v if v else None
    except Exception:
        return None


GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY") or read_win_env("GEMINI_API_KEY")
if not GEMINI_API_KEY:
    raise RuntimeError("❌ GEMINI_API_KEY가 없습니다. 환경변수 설정을 확인하세요.")

client = genai.Client(api_key=GEMINI_API_KEY)


def google_auth_drive() -> Credentials:
    """Google Drive Readonly OAuth"""
    creds = None
    if os.path.exists(TOKEN_PATH):
        creds = Credentials.from_authorized_user_file(TOKEN_PATH, SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not os.path.exists(CLIENT_SECRET_PATH):
                raise RuntimeError(f"❌ client_secret.json을 찾을 수 없음: {CLIENT_SECRET_PATH}")
            flow = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRET_PATH, SCOPES)
            creds = flow.run_local_server(port=0)

        with open(TOKEN_PATH, "w", encoding="utf-8") as f:
            f.write(creds.to_json())

    return creds


# =========================
# 🧩 3. 유틸
# =========================
def ensure_dirs():
    Path(OUT_DIR).mkdir(parents=True, exist_ok=True)
    Path(os.path.join(OUT_DIR, "images")).mkdir(parents=True, exist_ok=True)


def load_state() -> Dict:
    """state.json 로드"""
    if not os.path.exists(STATE_FILE):
        return {"processed_ids": []}
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {"processed_ids": []}
        if "processed_ids" not in data or not isinstance(data["processed_ids"], list):
            data["processed_ids"] = []
        return data
    except Exception:
        return {"processed_ids": []}


def save_state(processed_ids: List[str]):
    """state.json 저장"""
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump({"processed_ids": processed_ids}, f, ensure_ascii=False, indent=2)


def mime_to_ext(mime: str) -> str:
    """mimeType -> 파일 확장자"""
    m = (mime or "").lower()
    if "jpeg" in m or "jpg" in m:
        return "jpg"
    if "png" in m:
        return "png"
    if "webp" in m:
        return "webp"
    if "gif" in m:
        return "gif"
    # heic/heif는 브라우저 호환 애매하니 일단 확장자 그대로 저장(보일 수도/안 보일 수도)
    if "heic" in m:
        return "heic"
    if "heif" in m:
        return "heif"
    return "bin"


def download_drive_file_bytes(drive, file_id: str) -> bytes:
    """Drive 파일을 chunk 끝까지 다운로드해서 bytes로 반환"""
    request = drive.files().get_media(fileId=file_id)
    fh = io.BytesIO()
    downloader = MediaIoBaseDownload(fh, request)

    done = False
    while not done:
        status, done = downloader.next_chunk()

    return fh.getvalue()


def extract_json_object(text: str) -> Optional[dict]:
    """
    Gemini 결과에서 JSON 오브젝트만 최대한 안전하게 추출.
    - ```json ... ``` 제거
    - 본문에서 { ... } 덩어리만 잡기
    """
    if not text:
        return None

    cleaned = text.strip()
    cleaned = cleaned.replace("```json", "").replace("```", "").strip()

    # 가장 바깥 { ... } 블록 추출
    m = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if not m:
        return None

    candidate = m.group(0).strip()

    try:
        return json.loads(candidate)
    except Exception:
        # 가끔 작은따옴표/트레일링 콤마가 섞이면 깨짐 → 최소한의 보정 시도
        # (완벽하지는 않지만 성공률 올라감)
        candidate2 = candidate

        # 작은 따옴표를 큰따옴표로 무식하게 바꾸면 오히려 더 깨질 수 있어서,
        # 여기서는 trailing comma만 제거 정도만 시도
        candidate2 = re.sub(r",\s*([\]}])", r"\1", candidate2)

        try:
            return json.loads(candidate2)
        except Exception:
            return None


# =========================
# 🤖 4. AI 생성
# =========================
def ai_make_title_and_captions(images: List[Dict]) -> Dict:
    """
    Gemini AI가 사진을 보고 제목과 캡션을 만듦.
    리턴 형식:
    { "title": "...", "captions": ["..", "..", ...] }  (captions 길이 = 이미지 개수로 보정)
    """
    parts = [types.Part.from_bytes(data=img["bytes"], mime_type=img["mime"]) for img in images]
    n = len(images)

    prompt = (
        f"사진 {n}장에 대한 블로그 제목 1개와 각 사진별 2줄 설명을 만들어줘.\n"
        f"반드시 JSON만 출력해.\n"
        f'형식은 정확히 이거: {{"title":"제목","captions":["사진1 설명(2줄)","사진2 설명(2줄)", ...]}}\n'
        f"captions 배열 길이는 반드시 {n}개여야 해."
    )

    try:
        res = client.models.generate_content(model=MODEL_NAME, contents=parts + [prompt])
        raw = (res.text or "").strip()

        data = extract_json_object(raw)
        if not data:
            raise ValueError("JSON 파싱 실패")

        title = str(data.get("title") or "오늘의 사진 기록")
        captions = data.get("captions") or []
        if not isinstance(captions, list):
            captions = []

        # ✅ captions 길이 보정 (zip에서 누락 방지)
        if len(captions) < n:
            captions += [""] * (n - len(captions))
        captions = captions[:n]
        captions = [str(x) for x in captions]

        return {"title": title, "captions": captions}

    except Exception as e:
        print(f"⚠️ AI 생성 실패: {e}")
        return {"title": "오늘의 사진 기록", "captions": ["설명을 생성하지 못했습니다."] * n}


# =========================
# 🧾 5. index.html 생성
# =========================
def generate_index_html(repo_dir: str):
    """폴더 내 HTML 파일들을 읽어 메인 목록(index.html) 생성"""
    print("🔍 index.html(메인 목록) 생성 중...")

    html_files = [f for f in os.listdir(repo_dir) if f.endswith(".html") and f != "index.html"]
    html_files.sort(reverse=True)  # 파일명 역순 = 최신 우선(타임스탬프 쓰면 OK)

    cards = []
    for filename in html_files:
        path = os.path.join(repo_dir, filename)
        display_title = filename

        try:
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()
                match = re.search(r"<title>(.*?)</title>", content, re.IGNORECASE | re.DOTALL)
                if match:
                    display_title = match.group(1).strip()
        except Exception:
            pass

        # HTML escape (제목에 특수문자 들어가도 안전)
        safe_title = html.escape(display_title)
        safe_file = html.escape(filename)

        cards.append(f"""
        <div class="post-card">
            <a href="{safe_file}">{safe_title}</a>
            <div class="meta">{safe_file}</div>
        </div>
        """)

    cards_html = "\n".join(cards) if cards else "<p class='empty'>아직 게시글이 없습니다.</p>"

    full_index_html = f"""<!doctype html>
<html lang="ko">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>나의 블로그 포스트 목록</title>
    <style>
        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            background: #f4f7f6;
            padding: 40px 20px;
            color: #333;
            margin: 0;
        }}
        .container {{
            max-width: 720px;
            margin: 0 auto;
        }}
        h1 {{
            border-bottom: 2px solid #3b82f6;
            padding-bottom: 10px;
            margin-bottom: 24px;
            font-size: 28px;
        }}
        .post-card {{
            background: white;
            padding: 18px 18px;
            margin-bottom: 14px;
            border-radius: 14px;
            box-shadow: 0 2px 10px rgba(0,0,0,0.05);
            transition: 0.2s;
            border: 1px solid rgba(0,0,0,0.06);
        }}
        .post-card:hover {{
            transform: translateY(-2px);
            box-shadow: 0 6px 18px rgba(0,0,0,0.10);
            border-color: rgba(59,130,246,0.5);
        }}
        .post-card a {{
            text-decoration: none;
            color: #2563eb;
            font-weight: 800;
            font-size: 1.05rem;
            display: inline-block;
            margin-bottom: 6px;
        }}
        .meta {{
            font-size: 12px;
            color: #999;
        }}
        .empty {{
            background: white;
            padding: 16px;
            border-radius: 12px;
            border: 1px dashed rgba(0,0,0,0.15);
            color: #666;
        }}
        .footer {{
            text-align: center;
            color: #888;
            margin-top: 36px;
            font-size: 0.85rem;
        }}
    </style>
</head>
<body>
    <div class="container">
        <h1>📝 포스트 목록</h1>
        {cards_html}
        <div class="footer">업데이트: {time.strftime('%Y-%m-%d %H:%M:%S')}</div>
    </div>
</body>
</html>
"""

    with open(os.path.join(repo_dir, "index.html"), "w", encoding="utf-8") as f:
        f.write(full_index_html)

    print("✅ index.html 파일 쓰기 완료!")


# =========================
# 🧷 6. Git 자동 push
# =========================
def git_commit_push(repo_dir: str, message: str):
    """수정된 모든 파일을 GitHub로 올림 (안전하게 list args로 실행)"""
    def run(args: List[str]) -> Tuple[int, str, str]:
        r = subprocess.run(args, cwd=repo_dir, text=True, capture_output=True)
        return r.returncode, (r.stdout or ""), (r.stderr or "")

    code, out, err = run(["git", "add", "."])
    if code != 0:
        print("❌ git add 실패:", err or out)
        return

    code, out, err = run(["git", "commit", "-m", message])
    if code != 0:
        # nothing to commit이면 정상 케이스
        if "nothing to commit" in (out + err).lower():
            print("ℹ️ 변경사항 없음 (nothing to commit)")
            return
        print("❌ git commit 실패:", err or out)
        return

    code, out, err = run(["git", "push"])
    if code != 0:
        print("❌ git push 실패:", err or out)
        return

    print("🚀 GitHub 동기화 완료")


# =========================
# 📥 7. Drive 파일 목록 가져오기 (페이지네이션 포함)
# =========================
def list_all_images_in_folder(drive, folder_id: str) -> List[Dict]:
    """
    폴더 내 이미지 파일 전체를 페이지네이션으로 가져옴.
    createdTime asc 정렬.
    """
    query = f"'{folder_id}' in parents and trashed=false and mimeType contains 'image/'"
    all_files = []
    page_token = None

    while True:
        resp = drive.files().list(
            q=query,
            fields="nextPageToken, files(id,name,mimeType,createdTime)",
            orderBy="createdTime asc",
            pageSize=1000,
            pageToken=page_token
        ).execute()

        all_files.extend(resp.get("files", []))
        page_token = resp.get("nextPageToken")
        if not page_token:
            break

    return all_files


# =========================
# 🚀 8. 메인 실행
# =========================
def main():
    ensure_dirs()

    creds = google_auth_drive()
    drive = build("drive", "v3", credentials=creds)

    state = load_state()
    processed_ids: List[str] = state.get("processed_ids", [])

    # 1) 드라이브 이미지 전체 조회
    all_files = list_all_images_in_folder(drive, REAL_FOLDER_ID)

    # 2) 신규 파일만 필터
    new_files = [f for f in all_files if f.get("id") and f["id"] not in processed_ids]

    if not new_files:
        print("✅ 새로 추가된 이미지가 없습니다. 목록만 최신화합니다.")
    else:
        print(f"🆕 신규 이미지 {len(new_files)}개 발견")

        # 사진 묶음 처리 (MAX_PHOTOS_PER_POST장씩)
        for idx in range(0, len(new_files), MAX_PHOTOS_PER_POST):
            batch = new_files[idx: idx + MAX_PHOTOS_PER_POST]

            # 고유 post_id (타임스탬프 기반)
            post_id = int(time.time() * 1000) + idx  # ms 단위로 더 안전

            # 3) 이미지 다운로드 (bytes)
            images_for_ai = []
            for f in batch:
                fid = f["id"]
                mime = f.get("mimeType") or "application/octet-stream"

                try:
                    data = download_drive_file_bytes(drive, fid)
                except Exception as e:
                    print(f"⚠️ 다운로드 실패: {f.get('name')} ({fid}) - {e}")
                    continue

                images_for_ai.append({"bytes": data, "mime": mime, "id": fid, "name": f.get("name", "")})

            if not images_for_ai:
                print("⚠️ 이 배치에서 다운로드 성공한 이미지가 없음. 스킵.")
                continue

            # 4) Gemini 호출 (제목/캡션)
            ai_data = ai_make_title_and_captions(images_for_ai)
            title = ai_data.get("title") or "오늘의 사진 기록"
            captions = ai_data.get("captions") or [""] * len(images_for_ai)

            # 5) 개별 포스트 HTML 생성 + 이미지 저장
            post_blocks = ""
            for i, (img, cap) in enumerate(zip(images_for_ai, captions)):
                ext = mime_to_ext(img["mime"])
                img_name = f"img_{post_id}_{i}.{ext}"
                img_path = os.path.join(OUT_DIR, "images", img_name)

                with open(img_path, "wb") as f:
                    f.write(img["bytes"])

                safe_cap = html.escape(cap).replace("\n", "<br>")
                post_blocks += f"""
                <div style="background:#fff; padding:15px; border-radius:15px; margin-bottom:20px; box-shadow:0 4px 10px rgba(0,0,0,0.05); border:1px solid rgba(0,0,0,0.06);">
                    <img src="images/{html.escape(img_name)}" style="width:100%; border-radius:10px; display:block;">
                    <p style="line-height:1.6; margin-top:10px; white-space:normal;">{safe_cap}</p>
                </div>"""

            safe_title = html.escape(title)

            full_post_html = f"""<!doctype html>
<html lang="ko">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>{safe_title}</title>
    <style>
        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            max-width: 760px;
            margin: 0 auto;
            padding: 22px 16px;
            background: #f6f7fb;
            color: #111827;
        }}
        h1 {{
            margin-top: 6px;
            margin-bottom: 18px;
            font-size: 26px;
        }}
        .back {{
            display: inline-block;
            margin-top: 6px;
            margin-bottom: 10px;
            color: #2563eb;
            font-weight: 800;
            text-decoration: none;
        }}
    </style>
</head>
<body>
    <a class="back" href="index.html">🔙 목록으로 돌아가기</a>
    <h1>{safe_title}</h1>
    {post_blocks}
</body>
</html>
"""

            post_filename = f"{post_id}.html"
            with open(os.path.join(OUT_DIR, post_filename), "w", encoding="utf-8") as f:
                f.write(full_post_html)

            print(f"✅ 포스트 생성: {post_filename} (이미지 {len(images_for_ai)}장)")

            # 6) processed_ids 갱신
            processed_ids.extend([img["id"] for img in images_for_ai])

        # 상태 저장
        save_state(processed_ids)

    # 7) index.html 최신화
    generate_index_html(OUT_DIR)

    # 8) GitHub push
    git_commit_push(OUT_DIR, "auto: update posts and index.html")


if __name__ == "__main__":
    main()
