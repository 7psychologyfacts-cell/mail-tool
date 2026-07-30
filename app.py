"""
KAVACH MAIL — single-file backend
Real IMAP engine + zip-stream / Google Drive direct-link downloads.
Everything (config, security, IMAP, Drive, zip, routes) lives in this one
file on purpose, so it's a 3-file GitHub repo: app.py + index.html + requirements.txt.
"""
from __future__ import annotations

import email as email_lib
import imaplib
import io
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from email.header import decode_header
from email.utils import parseaddr, parsedate_to_datetime
from typing import Iterator, Optional, Literal

import jwt
from cryptography.fernet import Fernet
from fastapi import FastAPI, Depends, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, FileResponse, HTMLResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

from zipstream import ZipStream, ZIP_DEFLATED

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from google.auth.transport.requests import Request as GoogleAuthRequest
from googleapiclient.discovery import build as google_build
from googleapiclient.http import MediaIoBaseUpload


# ============================================================================
# CONFIG — set these as environment variables (Vercel: Project → Settings →
# Environment Variables). Nothing is hardcoded.
# ============================================================================

JWT_SECRET = os.environ["JWT_SECRET"]                    # required
FERNET_KEY = os.environ["FERNET_KEY"]                     # required
JWT_ALGORITHM = "HS256"
JWT_EXPIRE_MINUTES = 60 * 12

GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")
GOOGLE_REDIRECT_URI = os.environ.get("GOOGLE_REDIRECT_URI", "http://localhost:8000/api/auth/google/callback")
DRIVE_FOLDER_NAME = os.environ.get("DRIVE_FOLDER_NAME", "Kavach Mail Exports")

DIRECT_STREAM_THRESHOLD_BYTES = int(os.environ.get("DIRECT_STREAM_THRESHOLD_BYTES", 300 * 1024 * 1024))
MAX_DOWNLOAD_BYTES = int(os.environ.get("MAX_DOWNLOAD_BYTES", 9 * 1024 * 1024 * 1024))  # 9GB
UPLOAD_CHUNK_SIZE_BYTES = int(os.environ.get("UPLOAD_CHUNK_SIZE_BYTES", 8 * 1024 * 1024))
ALLOWED_ORIGINS = [o.strip() for o in os.environ.get("ALLOWED_ORIGINS", "*").split(",") if o.strip()]

_fernet = Fernet(FERNET_KEY.encode())
_bearer = HTTPBearer(auto_error=False)

imaplib._MAXLINE = 10_000_000

KNOWN_HOSTS = {
    "gmail.com": "imap.gmail.com",
    "googlemail.com": "imap.gmail.com",
    "outlook.com": "outlook.office365.com",
    "hotmail.com": "outlook.office365.com",
    "office365.com": "outlook.office365.com",
}


# ============================================================================
# SECURITY — JWT session (stateless: carries encrypted IMAP pw + Drive token)
# ============================================================================

def encrypt(value: str) -> str:
    return _fernet.encrypt(value.encode()).decode()


def decrypt(token: str) -> str:
    return _fernet.decrypt(token.encode()).decode()


def create_session_token(payload: dict) -> str:
    to_encode = payload.copy()
    to_encode["exp"] = datetime.now(timezone.utc) + timedelta(minutes=JWT_EXPIRE_MINUTES)
    to_encode["iat"] = datetime.now(timezone.utc)
    return jwt.encode(to_encode, JWT_SECRET, algorithm=JWT_ALGORITHM)


class Session:
    def __init__(self, claims: dict):
        self.claims = claims

    @property
    def email(self) -> str:
        return self.claims["email"]

    @property
    def imap_host(self) -> str:
        return self.claims["imap_host"]

    @property
    def imap_port(self) -> int:
        return self.claims["imap_port"]

    @property
    def imap_password(self) -> str:
        return decrypt(self.claims["imap_pw_enc"])

    @property
    def drive_refresh_token(self) -> Optional[str]:
        enc = self.claims.get("drive_rt_enc")
        return decrypt(enc) if enc else None


def get_session(creds: Optional[HTTPAuthorizationCredentials] = Depends(_bearer)) -> Session:
    if creds is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing bearer token")
    try:
        claims = jwt.decode(creds.credentials, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(401, "Session expired, please sign in again")
    except jwt.InvalidTokenError:
        raise HTTPException(401, "Invalid session token")
    return Session(claims)


# ============================================================================
# IMAP ENGINE — real connection, search, fetch (headers-only for listing)
# ============================================================================

class ImapAuthError(Exception):
    pass


def resolve_host(email_addr: str, explicit_host: Optional[str]) -> str:
    if explicit_host:
        return explicit_host
    domain = email_addr.split("@")[-1].lower()
    return KNOWN_HOSTS.get(domain, f"mail.{domain}")


def imap_connect(email_addr: str, password: str, host: str, port: int = 993) -> imaplib.IMAP4_SSL:
    try:
        conn = imaplib.IMAP4_SSL(host, port)
        conn.login(email_addr, password)
        return conn
    except imaplib.IMAP4.error as e:
        raise ImapAuthError(str(e))


def _decode(raw) -> str:
    if raw is None:
        return ""
    parts = decode_header(raw)
    out = []
    for text, enc in parts:
        out.append(text.decode(enc or "utf-8", errors="replace") if isinstance(text, bytes) else text)
    return "".join(out)


def _parse_addr_header(raw: str) -> tuple[str, str]:
    name, addr = parseaddr(raw or "")
    return _decode(name) or addr, addr


@dataclass
class Folder:
    name: str
    display_name: str
    unread: int = 0
    total: int = 0


def list_folders(conn: imaplib.IMAP4_SSL) -> list[Folder]:
    status_, boxes = conn.list()
    folders = []
    if status_ != "OK":
        return folders
    for raw in boxes:
        line = raw.decode(errors="replace")
        m = re.search(r'"(?:/|\.)"\s+"?([^"]+)"?$', line)
        if not m or "\\Noselect" in line:
            continue
        name = m.group(1)
        try:
            conn.select(name, readonly=True)
            _, total_data = conn.search(None, "ALL")
            total = len(total_data[0].split()) if total_data[0] else 0
            _, unread_data = conn.search(None, "UNSEEN")
            unread = len(unread_data[0].split()) if unread_data[0] else 0
        except Exception:
            total, unread = 0, 0
        folders.append(Folder(name=name, display_name=name.split("/")[-1], unread=unread, total=total))
    return folders


def build_search_criteria(query, from_addr, to_addr, subject, date_from, date_to) -> list[str]:
    crit: list[str] = []
    if from_addr:
        crit += ["FROM", f'"{from_addr}"']
    if to_addr:
        crit += ["TO", f'"{to_addr}"']
    if subject:
        crit += ["SUBJECT", f'"{subject}"']
    if query:
        crit += ["TEXT", f'"{query}"']
    if date_from:
        crit += ["SINCE", datetime.strptime(date_from, "%Y-%m-%d").strftime("%d-%b-%Y")]
    if date_to:
        crit += ["BEFORE", datetime.strptime(date_to, "%Y-%m-%d").strftime("%d-%b-%Y")]
    return crit or ["ALL"]


def imap_search(conn: imaplib.IMAP4_SSL, folder: str, criteria: list[str]) -> list[str]:
    conn.select(folder, readonly=True)
    status_, data = conn.uid("search", None, *criteria)
    if status_ != "OK" or not data or not data[0]:
        return []
    return [uid.decode() for uid in data[0].split()]


@dataclass
class AttachmentMeta:
    part_id: str
    filename: str
    size_bytes: int
    content_type: str


@dataclass
class MessageSummary:
    uid: str
    from_name: str
    from_email: str
    to_email: str
    subject: str
    snippet: str
    date_iso: str
    read: bool
    size_bytes: int
    has_attachment: bool
    attachments: list[AttachmentMeta] = field(default_factory=list)


def _parse_bodystructure_attachments(meta_line: bytes) -> list[AttachmentMeta]:
    text = meta_line.decode(errors="replace")
    results = []
    for i, m in enumerate(re.finditer(r'"(?:name|filename)"\s+"([^"]+)"', text, flags=re.IGNORECASE)):
        fname = m.group(1)
        size_m = re.search(r'"' + re.escape(fname) + r'"\s+\)\s+\S+\s+(\d+)', text)
        ctype_m = re.search(r'\("(\w+)"\s+"(\w+)"', text)
        ctype = f"{ctype_m.group(1)}/{ctype_m.group(2)}".lower() if ctype_m else "application/octet-stream"
        results.append(AttachmentMeta(str(i + 2), fname, int(size_m.group(1)) if size_m else 0, ctype))
    return results


def fetch_summaries(conn: imaplib.IMAP4_SSL, folder: str, uids: list[str]) -> list[MessageSummary]:
    if not uids:
        return []
    conn.select(folder, readonly=True)
    status_, data = conn.uid(
        "fetch", ",".join(uids),
        "(FLAGS RFC822.SIZE BODY.PEEK[HEADER.FIELDS (FROM TO SUBJECT DATE)] BODYSTRUCTURE)",
    )
    if status_ != "OK":
        return []
    out: list[MessageSummary] = []
    for item in data:
        if not isinstance(item, tuple):
            continue
        meta_line, header_bytes = item
        uid_m = re.search(rb"UID (\d+)", meta_line)
        size_m = re.search(rb"RFC822\.SIZE (\d+)", meta_line)
        flags_m = re.search(rb"FLAGS \(([^)]*)\)", meta_line)
        uid = uid_m.group(1).decode() if uid_m else "?"
        size_bytes = int(size_m.group(1)) if size_m else 0
        read = "\\Seen" in (flags_m.group(1).decode() if flags_m else "")

        msg = email_lib.message_from_bytes(header_bytes)
        from_name, from_email = _parse_addr_header(msg.get("From"))
        _, to_email = _parse_addr_header(msg.get("To"))
        subject = _decode(msg.get("Subject")) or "(no subject)"
        date_raw = msg.get("Date")
        try:
            date_iso = parsedate_to_datetime(date_raw).isoformat() if date_raw else ""
        except Exception:
            date_iso = ""

        attachments = _parse_bodystructure_attachments(meta_line)
        out.append(MessageSummary(uid, from_name, from_email, to_email, subject, "", date_iso,
                                   read, size_bytes, len(attachments) > 0, attachments))
    return out


def fetch_raw_message(conn: imaplib.IMAP4_SSL, folder: str, uid: str) -> bytes:
    conn.select(folder, readonly=True)
    status_, data = conn.uid("fetch", uid, "(RFC822)")
    if status_ != "OK" or not data or not data[0]:
        raise ValueError(f"Could not fetch message uid={uid}")
    return data[0][1]


def fetch_message_detail(conn: imaplib.IMAP4_SSL, folder: str, uid: str) -> dict:
    raw = fetch_raw_message(conn, folder, uid)
    msg = email_lib.message_from_bytes(raw)
    from_name, from_email = _parse_addr_header(msg.get("From"))
    _, to_email = _parse_addr_header(msg.get("To"))
    subject = _decode(msg.get("Subject")) or "(no subject)"
    date_raw = msg.get("Date")
    try:
        date_iso = parsedate_to_datetime(date_raw).isoformat() if date_raw else ""
    except Exception:
        date_iso = ""

    body_html, body_text = None, None
    attachments: list[AttachmentMeta] = []
    part_idx = 0
    for part in msg.walk():
        if part.is_multipart():
            continue
        part_idx += 1
        fname = part.get_filename()
        ctype = part.get_content_type()
        if fname:
            payload = part.get_payload(decode=True) or b""
            attachments.append(AttachmentMeta(str(part_idx), _decode(fname), len(payload), ctype))
        elif ctype == "text/html" and body_html is None:
            payload = part.get_payload(decode=True) or b""
            body_html = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
        elif ctype == "text/plain" and body_text is None:
            payload = part.get_payload(decode=True) or b""
            body_text = payload.decode(part.get_content_charset() or "utf-8", errors="replace")

    return {"uid": uid, "from_name": from_name, "from_email": from_email, "to_email": to_email,
            "subject": subject, "date_iso": date_iso, "body_html": body_html, "body_text": body_text,
            "attachments": attachments}


def fetch_attachment_bytes(conn, folder: str, uid: str, filename: str) -> tuple[bytes, str]:
    raw = fetch_raw_message(conn, folder, uid)
    msg = email_lib.message_from_bytes(raw)
    for part in msg.walk():
        if part.is_multipart():
            continue
        fname = part.get_filename()
        if fname and _decode(fname) == filename:
            return part.get_payload(decode=True) or b"", part.get_content_type()
    raise FileNotFoundError(f"Attachment '{filename}' not found on uid={uid}")


# ============================================================================
# ZIP STREAMING — builds a zip as a generator, one item at a time (no full
# buffering), used for BOTH direct-to-browser and Drive-upload paths.
# ============================================================================

def build_zip_chunks(items: Iterator[tuple[str, bytes]]) -> Iterator[bytes]:
    zs = ZipStream(compress_type=ZIP_DEFLATED, sized=False)
    for name, data in items:
        zs.add(data, arcname=name)
    yield from zs


def estimate_zip_overhead(total_raw_bytes: int, item_count: int) -> int:
    return int(total_raw_bytes * 1.02) + (item_count * 128)


# ============================================================================
# GOOGLE DRIVE — OAuth + memory-safe resumable upload + direct link
# ============================================================================

DRIVE_SCOPES = ["https://www.googleapis.com/auth/drive.file"]


def drive_build_flow() -> Flow:
    client_config = {"web": {
        "client_id": GOOGLE_CLIENT_ID, "client_secret": GOOGLE_CLIENT_SECRET,
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
        "redirect_uris": [GOOGLE_REDIRECT_URI],
    }}
    flow = Flow.from_client_config(client_config, scopes=DRIVE_SCOPES)
    flow.redirect_uri = GOOGLE_REDIRECT_URI
    return flow


def drive_get_authorize_url() -> str:
    flow = drive_build_flow()
    auth_url, _state = flow.authorization_url(access_type="offline", prompt="consent",
                                               include_granted_scopes="true")
    return auth_url


def drive_exchange_code(code: str) -> str:
    flow = drive_build_flow()
    flow.fetch_token(code=code)
    creds = flow.credentials
    if not creds.refresh_token:
        raise ValueError("No refresh_token returned — revoke app access in Google Account and reconnect.")
    return creds.refresh_token


def drive_credentials(refresh_token: str) -> Credentials:
    creds = Credentials(token=None, refresh_token=refresh_token,
                         token_uri="https://oauth2.googleapis.com/token",
                         client_id=GOOGLE_CLIENT_ID, client_secret=GOOGLE_CLIENT_SECRET,
                         scopes=DRIVE_SCOPES)
    creds.refresh(GoogleAuthRequest())
    return creds


class _IteratorStream(io.RawIOBase):
    """Lets MediaIoBaseUpload read our zip generator without materialising
    the whole archive in memory."""
    def __init__(self, chunks: Iterator[bytes]):
        self._chunks, self._buf, self._eof = chunks, b"", False

    def readable(self) -> bool:
        return True

    def readinto(self, b):
        size = len(b)
        while len(self._buf) < size and not self._eof:
            try:
                self._buf += next(self._chunks)
            except StopIteration:
                self._eof = True
                break
        chunk, self._buf = self._buf[:size], self._buf[size:]
        b[:len(chunk)] = chunk
        return len(chunk)


def _drive_get_or_create_folder(service, folder_name: str) -> str:
    q = f"name = '{folder_name}' and mimeType = 'application/vnd.google-apps.folder' and trashed = false"
    res = service.files().list(q=q, spaces="drive", fields="files(id)").execute()
    files = res.get("files", [])
    if files:
        return files[0]["id"]
    created = service.files().create(
        body={"name": folder_name, "mimeType": "application/vnd.google-apps.folder"}, fields="id"
    ).execute()
    return created["id"]


def drive_upload_stream(refresh_token: str, filename: str, chunks: Iterator[bytes]) -> dict:
    creds = drive_credentials(refresh_token)
    service = google_build("drive", "v3", credentials=creds, cache_discovery=False)
    folder_id = _drive_get_or_create_folder(service, DRIVE_FOLDER_NAME)

    media = MediaIoBaseUpload(_IteratorStream(chunks), mimetype="application/zip",
                               chunksize=UPLOAD_CHUNK_SIZE_BYTES, resumable=True)
    request = service.files().create(body={"name": filename, "parents": [folder_id]},
                                      media_body=media, fields="id, name, size")
    response = None
    while response is None:
        _status, response = request.next_chunk()

    file_id = response["id"]
    service.permissions().create(fileId=file_id, body={"type": "anyone", "role": "reader"}).execute()
    return {"id": file_id, "name": response.get("name", filename),
            "size_bytes": int(response["size"]) if response.get("size") else None,
            "direct_download_url": f"https://drive.google.com/uc?export=download&id={file_id}"}


def drive_get_quota(refresh_token: str) -> dict:
    creds = drive_credentials(refresh_token)
    service = google_build("drive", "v3", credentials=creds, cache_discovery=False)
    q = service.about().get(fields="storageQuota").execute().get("storageQuota", {})
    return {"used_bytes": int(q.get("usage", 0)), "total_bytes": int(q["limit"]) if q.get("limit") else None}


# ============================================================================
# SCHEMAS
# ============================================================================

class ImapLoginRequest(BaseModel):
    email: str
    password: str
    imap_host: Optional[str] = None
    imap_port: int = 993


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    email: str
    drive_connected: bool = False


class FolderInfo(BaseModel):
    name: str
    display_name: str
    unread: int
    total: int


class SearchQuery(BaseModel):
    folder: str = "INBOX"
    query: Optional[str] = None
    from_addr: Optional[str] = None
    to_addr: Optional[str] = None
    subject: Optional[str] = None
    has_attachment: Optional[bool] = None
    date_from: Optional[str] = None
    date_to: Optional[str] = None
    page: int = 1
    page_size: int = 25


class AttachmentMetaOut(BaseModel):
    part_id: str
    filename: str
    size_bytes: int
    content_type: str


class EmailSummary(BaseModel):
    uid: str
    folder: str
    from_name: str
    from_email: str
    to_email: str
    subject: str
    snippet: str
    date_iso: str
    read: bool
    has_attachment: bool
    attachments: list[AttachmentMetaOut] = []


class EmailDetail(EmailSummary):
    body_html: Optional[str] = None
    body_text: Optional[str] = None


class SearchResult(BaseModel):
    results: list[EmailSummary]
    total: int
    page: int
    page_size: int


class DownloadItem(BaseModel):
    uid: str
    folder: str
    part_ids: Optional[list[str]] = None


class DownloadRequest(BaseModel):
    items: list[DownloadItem]
    mode: Literal["auto", "zip_stream", "drive_link"] = "auto"
    archive_name: str = "kavach-mail-export"


class DownloadSizeEstimate(BaseModel):
    total_bytes: int
    item_count: int
    recommended_mode: Literal["zip_stream", "drive_link"]
    exceeds_limit: bool


class DownloadResponse(BaseModel):
    mode: Literal["zip_stream", "drive_link"]
    download_url: Optional[str] = None
    file_name: Optional[str] = None
    size_bytes: Optional[int] = None
    note: Optional[str] = None


class DriveAuthUrl(BaseModel):
    auth_url: str


class DriveStatus(BaseModel):
    connected: bool
    quota_used_bytes: Optional[int] = None
    quota_total_bytes: Optional[int] = None


# ============================================================================
# FASTAPI APP + ROUTES  (all under /api so index.html can be served at "/")
# ============================================================================

app = FastAPI(title="Kavach Mail Backend", version="1.0.0")
app.add_middleware(CORSMiddleware, allow_origins=ALLOWED_ORIGINS, allow_credentials=True,
                    allow_methods=["*"], allow_headers=["*"])


def _open_conn(session: Session):
    try:
        return imap_connect(session.email, session.imap_password, session.imap_host, session.imap_port)
    except ImapAuthError as e:
        raise HTTPException(401, f"IMAP reconnect failed: {e}. Please sign in again.")


@app.get("/", response_class=HTMLResponse)
def serve_frontend():
    path = os.path.join(os.path.dirname(__file__), "index.html")
    if os.path.exists(path):
        return FileResponse(path)
    return HTMLResponse("<h1>Kavach Mail backend is running.</h1><p>index.html not found next to app.py.</p>")


@app.get("/api/health")
def health():
    return {"status": "ok", "service": "kavach-mail-backend"}


# ---- Auth ----

@app.post("/api/auth/login", response_model=TokenResponse)
def login(body: ImapLoginRequest):
    host = resolve_host(body.email, body.imap_host)
    try:
        conn = imap_connect(body.email, body.password, host, body.imap_port)
        conn.logout()
    except ImapAuthError as e:
        raise HTTPException(401, f"IMAP login failed: {e}")
    token = create_session_token({
        "email": body.email, "imap_host": host, "imap_port": body.imap_port,
        "imap_pw_enc": encrypt(body.password),
    })
    return TokenResponse(access_token=token, email=body.email, drive_connected=False)


@app.get("/api/auth/google/authorize", response_model=DriveAuthUrl)
def google_authorize(session: Session = Depends(get_session)):
    if not GOOGLE_CLIENT_ID:
        raise HTTPException(500, "Google OAuth not configured on server")
    return DriveAuthUrl(auth_url=drive_get_authorize_url())


@app.get("/api/auth/google/callback")
def google_callback(code: str, state: str = ""):
    try:
        refresh_token = drive_exchange_code(code)
    except Exception as e:
        raise HTTPException(400, f"Google OAuth exchange failed: {e}")
    return {"refresh_token": refresh_token}


@app.post("/api/auth/attach-drive", response_model=TokenResponse)
def attach_drive(refresh_token: str, session: Session = Depends(get_session)):
    new_claims = dict(session.claims)
    new_claims["drive_rt_enc"] = encrypt(refresh_token)
    new_claims.pop("exp", None)
    new_claims.pop("iat", None)
    token = create_session_token(new_claims)
    return TokenResponse(access_token=token, email=session.email, drive_connected=True)


@app.get("/api/auth/drive/status", response_model=DriveStatus)
def drive_status(session: Session = Depends(get_session)):
    rt = session.drive_refresh_token
    if not rt:
        return DriveStatus(connected=False)
    try:
        q = drive_get_quota(rt)
        return DriveStatus(connected=True, quota_used_bytes=q["used_bytes"], quota_total_bytes=q["total_bytes"])
    except Exception:
        return DriveStatus(connected=False)


# ---- Mail ----

@app.get("/api/folders", response_model=list[FolderInfo])
def get_folders(session: Session = Depends(get_session)):
    conn = _open_conn(session)
    try:
        folders = list_folders(conn)
    finally:
        conn.logout()
    return [FolderInfo(name=f.name, display_name=f.display_name, unread=f.unread, total=f.total) for f in folders]


@app.post("/api/search", response_model=SearchResult)
def search_mail(q: SearchQuery, session: Session = Depends(get_session)):
    conn = _open_conn(session)
    try:
        criteria = build_search_criteria(q.query, q.from_addr, q.to_addr, q.subject, q.date_from, q.date_to)
        all_uids = list(reversed(imap_search(conn, q.folder, criteria)))
        total = len(all_uids)
        start = (q.page - 1) * q.page_size
        page_uids = all_uids[start:start + q.page_size]
        summaries = fetch_summaries(conn, q.folder, page_uids)
        if q.has_attachment is not None:
            summaries = [s for s in summaries if s.has_attachment == q.has_attachment]
    finally:
        conn.logout()

    results = [EmailSummary(
        uid=s.uid, folder=q.folder, from_name=s.from_name, from_email=s.from_email, to_email=s.to_email,
        subject=s.subject, snippet=s.snippet, date_iso=s.date_iso, read=s.read, has_attachment=s.has_attachment,
        attachments=[AttachmentMetaOut(**a.__dict__) for a in s.attachments],
    ) for s in summaries]
    return SearchResult(results=results, total=total, page=q.page, page_size=q.page_size)


@app.get("/api/email/{folder}/{uid}", response_model=EmailDetail)
def get_email(folder: str, uid: str, session: Session = Depends(get_session)):
    conn = _open_conn(session)
    try:
        d = fetch_message_detail(conn, folder, uid)
    except ValueError as e:
        raise HTTPException(404, str(e))
    finally:
        conn.logout()
    return EmailDetail(
        uid=d["uid"], folder=folder, from_name=d["from_name"], from_email=d["from_email"],
        to_email=d["to_email"], subject=d["subject"], snippet="", date_iso=d["date_iso"], read=True,
        has_attachment=len(d["attachments"]) > 0,
        attachments=[AttachmentMetaOut(**a.__dict__) for a in d["attachments"]],
        body_html=d["body_html"], body_text=d["body_text"],
    )


@app.get("/api/email/{folder}/{uid}/attachment/{filename}")
def download_single_attachment(folder: str, uid: str, filename: str, session: Session = Depends(get_session)):
    conn = _open_conn(session)
    try:
        data, ctype = fetch_attachment_bytes(conn, folder, uid, filename)
    except FileNotFoundError as e:
        raise HTTPException(404, str(e))
    finally:
        conn.logout()
    return StreamingResponse(io.BytesIO(data), media_type=ctype,
                              headers={"Content-Disposition": f'attachment; filename="{filename}"'})


# ---- Download engine ----

def _iter_items_as_zip_entries(session: Session, req: DownloadRequest) -> Iterator[tuple[str, bytes]]:
    conn = _open_conn(session)
    try:
        for item in req.items:
            raw = fetch_raw_message(conn, item.folder, item.uid)
            msg = email_lib.message_from_bytes(raw)
            subject = (msg.get("Subject") or f"message-{item.uid}").strip()
            safe_subject = "".join(c for c in subject if c.isalnum() or c in " ._-")[:80] or f"message-{item.uid}"

            if item.part_ids is None:
                yield f"{safe_subject} ({item.uid}).eml", raw
                for part in msg.walk():
                    if part.is_multipart():
                        continue
                    fname = part.get_filename()
                    if fname:
                        yield f"{safe_subject} ({item.uid})/{fname}", part.get_payload(decode=True) or b""
            else:
                part_idx = 0
                for part in msg.walk():
                    if part.is_multipart():
                        continue
                    part_idx += 1
                    if str(part_idx) not in item.part_ids:
                        continue
                    fname = part.get_filename() or f"part-{part_idx}"
                    yield f"{safe_subject} ({item.uid})/{fname}", part.get_payload(decode=True) or b""
    finally:
        conn.logout()


@app.post("/api/download/estimate", response_model=DownloadSizeEstimate)
def estimate(req: DownloadRequest, session: Session = Depends(get_session)):
    conn = _open_conn(session)
    total = 0
    try:
        by_folder: dict[str, list[str]] = {}
        for item in req.items:
            by_folder.setdefault(item.folder, []).append(item.uid)
        for folder, uids in by_folder.items():
            total += sum(s.size_bytes for s in fetch_summaries(conn, folder, uids))
    finally:
        conn.logout()

    total = estimate_zip_overhead(total, len(req.items))
    return DownloadSizeEstimate(
        total_bytes=total, item_count=len(req.items),
        recommended_mode="zip_stream" if total < DIRECT_STREAM_THRESHOLD_BYTES else "drive_link",
        exceeds_limit=total > MAX_DOWNLOAD_BYTES,
    )


@app.post("/api/download")
def download(req: DownloadRequest, session: Session = Depends(get_session)):
    if not req.items:
        raise HTTPException(400, "No items selected")

    mode = req.mode
    if mode == "auto":
        est = estimate(req, session)
        if est.exceeds_limit:
            raise HTTPException(413, f"Selection is ~{est.total_bytes / (1024**3):.1f} GB, over the "
                                      f"{MAX_DOWNLOAD_BYTES / (1024**3):.0f} GB limit. Select fewer items.")
        mode = est.recommended_mode

    zip_name = f"{req.archive_name}.zip"

    if mode == "zip_stream":
        chunks = build_zip_chunks(_iter_items_as_zip_entries(session, req))
        return StreamingResponse(chunks, media_type="application/zip",
                                  headers={"Content-Disposition": f'attachment; filename="{zip_name}"'})

    refresh_token = session.drive_refresh_token
    if not refresh_token:
        raise HTTPException(428, "This selection is large — connect Google Drive first "
                                  "(GET /api/auth/google/authorize) so we can give you a direct link.")

    chunks = build_zip_chunks(_iter_items_as_zip_entries(session, req))
    try:
        result = drive_upload_stream(refresh_token, zip_name, chunks)
    except Exception as e:
        raise HTTPException(502, f"Drive upload failed: {e}")

    return DownloadResponse(
        mode="drive_link", download_url=result["direct_download_url"], file_name=result["name"],
        size_bytes=result.get("size_bytes"),
        note="Files over ~100MB may show Google's 'can't scan for viruses' page — click 'Download anyway'.",
    )
