import os
import io
import json
import time
import subprocess
import re
import html
import random
from pathlib import Path
from typing import Optional, Dict, List, Tuple, Any

from google import genai
from google.genai import types

from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials

# =========================
# 🔧 1. 사용자 설정 (PORTABLE)
# =========================
REAL_FOLDER_ID = "1v5VE_BRLNUlkEk_nXHSQHdMN4TkjoUiT"   # 구글 드라이브 폴더 ID
MODEL_NAME = "gemini-2.0-flash"                        # 사용할 모델
MAX_PHOTOS_PER_POST = 5                                # 한 포스트당 사진 개수

# ✅ 레포/스크립트 위치 기반 (새 PC에서도 안 깨짐)
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = SCRIPT_DIR
STATE_FILE = os.path.join(OUT_DIR, "state.json")

# Google Drive scope
SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]

# ✅ 사용자 홈 기준 시크릿 저장 (새 PC 사용자명 달라도 OK)
USER_HOME = os.path.expanduser("~")
TOKEN_PATH = os.path.join(USER_HOME, ".secrets", "blog", "token_drive.json")
CLIENT_SECRET_PATH = os.path.join(USER_HOME, ".secrets", "blog", "client_secret.json")

# ✅ 토큰 저장 폴더 자동 생성
Path(os.path.dirname(TOKEN_PATH)).mkdir(parents=True, exist_ok=True)

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

# =========================
# 🧩 3. 유틸 (안정성/재시도/백오프)
# =========================
def ensure_dirs():
    Path(OUT_DIR).mkdir(parents=True, exist_ok=True)
    Path(os.path.join(OUT_DIR, "images")).mkdir(parents=True, exist_ok=True)


def load_state() -> Dict[str, Any]:
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
    # heic/heif는 변환을 시도할 것이지만, 일단 원본 확장자 유지
    if "heic" in m:
        return "heic"
    if "heif" in m:
        return "heif"
    return "bin"


def retry(
    fn,
    *,
    tries: int = 4,
    base_delay: float = 1.0,
    max_delay: float = 20.0,
    jitter: float = 0.25,
    retry_on: Tuple[type, ...] = (Exception,),
    label: str = "operation"
):
    """
    간단한 재시도 + 지수 백오프
    - tries: 총 시도 횟수
    - base_delay: 첫 대기
    - max_delay: 최대 대기
    - jitter: 랜덤 흔들기(동시 재시도 폭주 방지)
    """
    last_err = None
    for attempt in range(1, tries + 1):
        try:
            return fn()
        except retry_on as e:
            last_err = e
            if attempt == tries:
                break
            delay = min(max_delay, base_delay * (2 ** (attempt - 1)))
            delay = delay * (1.0 + random.uniform(-jitter, jitter))
            print(f"⚠️ {label} 실패 (시도 {attempt}/{tries}): {e} → {delay:.1f}s 후 재시도")
            time.sleep(max(0.1, delay))
    raise last_err  # type: ignore


def download_drive_file_bytes(drive, file_id: str) -> bytes:
    """Drive 파일을 chunk 끝까지 다운로드해서 bytes로 반환 (재시도 포함)"""
    def _do():
        request = drive.files().get_media(fileId=file_id)
        fh = io.BytesIO()
        downloader = MediaIoBaseDownload(fh, request)
        done = False
        while not done:
            status, done = downloader.next_chunk()
        return fh.getvalue()

    return retry(_do, tries=4, base_delay=1.0, max_delay=12.0, label=f"Drive download {file_id}")


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
        # trailing comma 제거 정도만 보정
        candidate2 = re.sub(r",\s*([\]}])", r"\1", candidate)
        try:
            return json.loads(candidate2)
        except Exception:
            return None


def normalize_caption(text: str) -> str:
    """캡션 텍스트를 2줄 느낌으로 정리 (너무 길면 적당히 정리)"""
    t = (text or "").strip()
    # 과하게 길면 줄바꿈 2줄 기준으로 잘라줌(대충 안전장치)
    if len(t) > 500:
        t = t[:500].rstrip() + "…"
    # 줄이 아예 없으면 2문장 느낌으로 줄바꿈 추가는 강제하지 않고 그대로 둠
    return t


# =========================
# 🖼️ 3-1. HEIC/HEIF → JPG 변환 (가능할 때만)
# =========================
def maybe_convert_heic_to_jpg(img_bytes: bytes, mime: str) -> Tuple[bytes, str, str]:
    """
    HEIC/HEIF면 JPG로 변환 시도.
    성공하면 (jpg_bytes, "image/jpeg", "jpg")
    실패하면 (원본_bytes, 원본_mime, 원본_ext)
    """
    m = (mime or "").lower()
    ext = mime_to_ext(mime)

    if ("heic" not in m) and ("heif" not in m) and (ext not in ("heic", "heif")):
        return img_bytes, mime, ext

    try:
        # pillow-heif가 설치되어 있으면 PIL에서 HEIF/HEIC 열 수 있음
        from PIL import Image  # pillow
        try:
            import pillow_heif  # noqa: F401
        except Exception:
            # pillow-heif 없으면 열기 확률 낮음 -> 변환 불가
            return img_bytes, mime, ext

        # pillow-heif는 import만으로도 등록되는 경우가 많음
        im = Image.open(io.BytesIO(img_bytes)).convert("RGB")
        out = io.BytesIO()
        im.save(out, format="JPEG", quality=92)
        return out.getvalue(), "image/jpeg", "jpg"
    except Exception:
        return img_bytes, mime, ext


# =========================
# 🤖 4. AI 생성 (스키마 + 재시도)
# =========================
def ai_make_title_and_captions(images: List[Dict]) -> Dict:
    """
    Gemini AI가 사진을 보고 제목과 캡션을 만듦.
    리턴 형식:
    { "title": "...", "captions": ["..", "..", ...] }  (captions 길이 = 이미지 개수로 보정)
    """
    n = len(images)
    parts = [types.Part.from_bytes(data=img["bytes"], mime_type=img["mime"]) for img in images]

    # ✅ JSON 외 출력 금지 강하게, 2줄 설명 요구
    prompt = (
        f"사진 {n}장에 대한 블로그 제목 1개와 각 사진별 '2줄 설명'을 만들어줘.\n"
        f"반드시 JSON만 출력해. 다른 텍스트, 마크다운, 코드블록 금지.\n"
        f'형식은 정확히: {{"title":"제목","captions":["사진1 설명(2줄)","사진2 설명(2줄)", ...]}}\n'
        f"captions 배열 길이는 반드시 {n}개.\n"
        f"각 captions 원소는 줄바꿈(\\n)을 포함해서 2줄로 써줘."
    )

    # ✅ response_schema (가능한 범위에서 파싱 안정성 ↑)
    response_schema = {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "captions": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": n,
                "maxItems": n
            }
        },
        "required": ["title", "captions"],
        "additionalProperties": False
    }

    def _do():
        # 일부 SDK는 config를 지원
        try:
            config = types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=response_schema,
                temperature=0.7,
            )
            res = client.models.generate_content(
                model=MODEL_NAME,
                contents=parts + [prompt],
                config=config
            )
        except Exception:
            # config 미지원/에러 시 기본 호출로 폴백
            res = client.models.generate_content(model=MODEL_NAME, contents=parts + [prompt])
        return res

    try:
        res = retry(_do, tries=4, base_delay=1.5, max_delay=25.0, label="Gemini generate_content")
        raw = (getattr(res, "text", None) or "").strip()

        data = extract_json_object(raw)
        if not data:
            # 어떤 경우엔 response_mime_type 적용 시 text가 아닌 구조로 올 수 있음
            # 그래도 안전하게 한 번 더 시도
            raise ValueError("JSON 파싱 실패")

        title = str(data.get("title") or "오늘의 사진 기록").strip()
        captions = data.get("captions") or []
        if not isinstance(captions, list):
            captions = []

        # 길이 보정
        if len(captions) < n:
            captions += [""] * (n - len(captions))
        captions = captions[:n]
        captions = [normalize_caption(str(x)) for x in captions]

        return {"title": title, "captions": captions}

    except Exception as e:
        print(f"⚠️ AI 생성 실패: {e}")
        return {"title": "오늘의 사진 기록", "captions": ["설명을 생성하지 못했습니다."] * n}


# =========================
# 🧾 5. index.html 생성 (썸네일+lazy+날짜)
# =========================
def try_extract_first_image_src(post_html: str) -> Optional[str]:
    """
    포스트 HTML에서 첫 번째 <img src="..."> 경로를 찾아서 반환
    """
    m = re.search(r'<img[^>]+src="([^"]+)"', post_html, re.IGNORECASE)
    if not m:
        return None
    return m.group(1)


def post_id_to_datetime_str(post_id: str) -> str:
    """
    파일명이 ms 타임스탬프라고 가정하고 날짜 문자열로 변환
    """
    try:
        ms = int(re.sub(r"\D", "", post_id))
        sec = ms / 1000.0
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(sec))
    except Exception:
        return ""


def generate_index_html(repo_dir: str):
    """폴더 내 HTML 파일들을 읽어 메인 목록(index.html) 생성"""
    print("🔍 index.html(메인 목록) 생성 중...")

    html_files = [f for f in os.listdir(repo_dir) if f.endswith(".html") and f != "index.html"]
    html_files.sort(reverse=True)

    cards = []
    for filename in html_files:
        path = os.path.join(repo_dir, filename)
        display_title = filename.replace(".html", "")
        thumb_src = None
        date_str = post_id_to_datetime_str(display_title)

        try:
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()

            # title
            match = re.search(r"<title>(.*?)</title>", content, re.IGNORECASE | re.DOTALL)
            if match:
                display_title = match.group(1).strip()

            # thumbnail
            thumb_src = try_extract_first_image_src(content)

        except Exception:
            pass

        safe_title = html.escape(display_title)
        safe_file = html.escape(filename)

        thumb_html = ""
        if thumb_src:
            thumb_html = f"""
            <div class="thumb">
              <img loading="lazy" src="{html.escape(thumb_src)}" alt="thumbnail">
            </div>
            """

        meta_bits = []
        if date_str:
            meta_bits.append(date_str)
        meta_bits.append(filename)
        meta_text = " · ".join(meta_bits)

        cards.append(f"""
        <a class="post-card" href="{safe_file}">
            {thumb_html}
            <div class="info">
                <div class="title">{safe_title}</div>
                <div class="meta">{html.escape(meta_text)}</div>
            </div>
        </a>
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
            padding: 36px 16px;
            color: #111827;
            margin: 0;
        }}
        .container {{
            max-width: 860px;
            margin: 0 auto;
        }}
        h1 {{
            margin: 0 0 18px 0;
            font-size: 28px;
        }}
        .grid {{
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
            gap: 14px;
        }}
        .post-card {{
            display: block;
            background: white;
            border-radius: 16px;
            overflow: hidden;
            border: 1px solid rgba(0,0,0,0.06);
            box-shadow: 0 2px 10px rgba(0,0,0,0.05);
            text-decoration: none;
            color: inherit;
            transition: 0.2s;
        }}
        .post-card:hover {{
            transform: translateY(-2px);
            box-shadow: 0 8px 20px rgba(0,0,0,0.10);
            border-color: rgba(59,130,246,0.5);
        }}
        .thumb {{
            aspect-ratio: 16 / 10;
            background: #f3f4f6;
        }}
        .thumb img {{
            width: 100%;
            height: 100%;
            object-fit: cover;
            display: block;
        }}
        .info {{
            padding: 14px 14px 16px 14px;
        }}
        .title {{
            font-weight: 900;
            font-size: 1.05rem;
            color: #2563eb;
            line-height: 1.25;
            margin-bottom: 8px;
        }}
        .meta {{
            font-size: 12px;
            color: #6b7280;
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
            margin-top: 28px;
            font-size: 0.85rem;
        }}
    </style>
</head>
<body>
    <div class="container">
        <h1>📝 포스트 목록</h1>
        <div class="grid">
            {cards_html}
        </div>
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
# 🔐 8. Google Auth (Drive Readonly OAuth)
# =========================
def google_auth_drive() -> Credentials:
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

        # 저장
        Path(os.path.dirname(TOKEN_PATH)).mkdir(parents=True, exist_ok=True)
        with open(TOKEN_PATH, "w", encoding="utf-8") as f:
            f.write(creds.to_json())

    return creds


# =========================
# 🚀 9. 메인 실행
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

        for idx in range(0, len(new_files), MAX_PHOTOS_PER_POST):
            batch = new_files[idx: idx + MAX_PHOTOS_PER_POST]
            post_id = int(time.time() * 1000) + idx

            images_for_ai = []
            for f in batch:
                fid = f["id"]
                mime = f.get("mimeType") or "application/octet-stream"

                try:
                    data = download_drive_file_bytes(drive, fid)
                except Exception as e:
                    print(f"⚠️ 다운로드 실패: {f.get('name')} ({fid}) - {e}")
                    continue

                # ✅ HEIC/HEIF면 JPG 변환 시도
                data2, mime2, ext2 = maybe_convert_heic_to_jpg(data, mime)

                images_for_ai.append({
                    "bytes": data2,
                    "mime": mime2,
                    "ext": ext2,
                    "id": fid,
                    "name": f.get("name", "")
                })

            if not images_for_ai:
                print("⚠️ 이 배치에서 다운로드 성공한 이미지가 없음. 스킵.")
                continue

            # 4) Gemini 호출 (제목/캡션) - 재시도 포함
            ai_data = ai_make_title_and_captions(images_for_ai)
            title = ai_data.get("title") or "오늘의 사진 기록"
            captions = ai_data.get("captions") or [""] * len(images_for_ai)

            # 5) 개별 포스트 HTML 생성 + 이미지 저장
            post_blocks = ""
            for i, (img, cap) in enumerate(zip(images_for_ai, captions)):
                ext = img.get("ext") or mime_to_ext(img["mime"])
                img_name = f"img_{post_id}_{i}.{ext}"
                img_path = os.path.join(OUT_DIR, "images", img_name)

                with open(img_path, "wb") as fw:
                    fw.write(img["bytes"])

                safe_cap = html.escape(cap).replace("\n", "<br>")
                post_blocks += f"""
                <div style="background:#fff; padding:15px; border-radius:15px; margin-bottom:20px; box-shadow:0 4px 10px rgba(0,0,0,0.05); border:1px solid rgba(0,0,0,0.06);">
                    <img loading="lazy" src="images/{html.escape(img_name)}" style="width:100%; border-radius:10px; display:block;">
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
        .meta {{
            font-size: 12px;
            color: #6b7280;
            margin-bottom: 14px;
        }}
    </style>
</head>
<body>
    <a class="back" href="index.html">🔙 목록으로 돌아가기</a>
    <h1>{safe_title}</h1>
    <div class="meta">{time.strftime('%Y-%m-%d %H:%M:%S')}</div>
    {post_blocks}
</body>
</html>
"""

            post_filename = f"{post_id}.html"
            with open(os.path.join(OUT_DIR, post_filename), "w", encoding="utf-8") as fw:
                fw.write(full_post_html)

            print(f"✅ 포스트 생성: {post_filename} (이미지 {len(images_for_ai)}장)")

            processed_ids.extend([img["id"] for img in images_for_ai])

        save_state(processed_ids)

    # index 최신화
    generate_index_html(OUT_DIR)

    # GitHub push
    git_commit_push(OUT_DIR, "auto: update posts and index.html")


if __name__ == "__main__":
    main()
