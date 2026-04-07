from __future__ import annotations

import argparse
import html
import json
import mimetypes
import os
import re
import sqlite3
import socket
import threading
import time
import traceback
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

try:
    from flask import Flask, Response, jsonify, request, send_from_directory
except ImportError:
    class _DummyFlask:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        def get(self, *args: Any, **kwargs: Any):
            def decorator(func):
                return func
            return decorator

        def post(self, *args: Any, **kwargs: Any):
            def decorator(func):
                return func
            return decorator

        def run(self, *args: Any, **kwargs: Any) -> None:
            raise RuntimeError("Flask is not installed. Web server mode is unavailable.")

    class _DummyRequest:
        headers: dict[str, str] = {}

        @staticmethod
        def get_data(cache: bool = False) -> bytes:
            return b""

    Flask = _DummyFlask
    Response = Any
    jsonify = lambda payload=None, *args, **kwargs: payload
    request = _DummyRequest()
    send_from_directory = lambda *args, **kwargs: None


BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(
    os.environ.get("DATA_DIR")
    or os.environ.get("RAILWAY_VOLUME_MOUNT_PATH")
    or BASE_DIR
).resolve()
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "assembly_rankings.db"
RESULT_DB_PATH = DATA_DIR / "assembly_rankings_result.db"
RESULT_DB_UPLOAD_PATH = DATA_DIR / "assembly_rankings_result.uploading.db"
REFRESH_STATUS_PATH = DATA_DIR / "refresh_status.json"
API_URL_PATH = BASE_DIR / "API_URL.txt"
ADMIN_UPLOAD_TOKEN = os.environ.get("ADMIN_UPLOAD_TOKEN", "").strip()
NAVER_CLIENT_ID = os.environ.get("NAVER_CLIENT_ID", "").strip()
NAVER_CLIENT_SECRET = os.environ.get("NAVER_CLIENT_SECRET", "").strip()
NAVER_NEWS_CACHE_HOURS = int(os.environ.get("NAVER_NEWS_CACHE_HOURS", "24"))
NEWS_KEYWORD_CACHE_VERSION = 4
DEFAULT_ASSEMBLY_NUMBER = 22
DEFAULT_ASSEMBLY_LABEL = f"제{DEFAULT_ASSEMBLY_NUMBER}대"
PAGE_SIZE = 1000
VOTE_WORKERS = 4
PAGE_FETCH_WORKERS = 6
DEFAULT_API_TIMEOUT = 60
VOTE_API_TIMEOUT = 15
VOTE_API_RETRIES = 2
ATTENDED_RESULTS = {"찬성", "반대", "기권"}
FALLBACK_API_CONFIG = {
    "key": "fc8d86af691f4f5798f7fe39595d1de9",
    "member_url": "https://open.assembly.go.kr/portal/openapi/ALLNAMEMBER",
    "bill_url": "https://open.assembly.go.kr/portal/openapi/ALLBILLV2",
    "vote_url": "https://open.assembly.go.kr/portal/openapi/nojepdqqaweusdfbi",
}

refresh_job_lock = threading.Lock()
refresh_job_state: dict[str, Any] = {
    "status": "idle",
    "started_at": None,
    "finished_at": None,
    "error": None,
    "last_synced_at": None,
}


def default_refresh_status() -> dict[str, Any]:
    return {
        "status": "idle",
        "stage": "idle",
        "message": "대기 중입니다.",
        "progress": 0,
        "progress_detail": None,
        "started_at": None,
        "finished_at": None,
        "error": None,
        "last_synced_at": None,
    }


def load_api_config() -> dict[str, str]:
    try:
        text = API_URL_PATH.read_text(encoding="utf-8")
        key = None
        urls: list[str] = []
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.lower().startswith("key:"):
                key = stripped.split(":", 1)[1].strip()
            elif stripped.startswith("https://open.assembly.go.kr/portal/openapi/"):
                urls.append(stripped.split("?", 1)[0])
        if not key or len(urls) < 3:
            raise ValueError("invalid API_URL.txt")
        return {
            "key": key,
            "member_url": next(url for url in urls if "ALLNAMEMBER" in url),
            "bill_url": next(url for url in urls if "ALLBILLV2" in url),
            "vote_url": next(url for url in urls if "nojepdqqaweusdfbi" in url),
        }
    except Exception:
        return FALLBACK_API_CONFIG.copy()


def fetch_api_page(
    endpoint: str,
    params: dict[str, Any],
    page_index: int,
    page_size: int,
    timeout: int = DEFAULT_API_TIMEOUT,
) -> dict[str, Any]:
    config = load_api_config()
    query = {
        "KEY": config["key"],
        "Type": "json",
        "pIndex": str(page_index),
        "pSize": str(page_size),
    }
    for key, value in params.items():
        if value is not None and value != "":
            query[key] = str(value)
    url = f"{endpoint}?{urllib.parse.urlencode(query)}"
    request = urllib.request.Request(url, headers={"User-Agent": "assembly-ranking/1.0"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    result = payload.get("RESULT")
    if result and result.get("CODE") not in {"INFO-000", "INFO-200"}:
        raise RuntimeError(result.get("MESSAGE", "OpenAPI error"))
    return payload


def extract_rows(payload: dict[str, Any], service_key: str) -> list[dict[str, Any]]:
    service = payload.get(service_key)
    if not service:
      return []
    row_block = next((item for item in service if isinstance(item, dict) and "row" in item), None)
    return row_block.get("row", []) if row_block else []


def extract_total_count(payload: dict[str, Any], service_key: str) -> int:
    service = payload.get(service_key)
    if not service:
        return 0
    head_block = next((item for item in service if isinstance(item, dict) and "head" in item), None)
    if not head_block:
        return 0
    total_entry = next((item for item in head_block["head"] if "list_total_count" in item), None)
    return int(total_entry["list_total_count"]) if total_entry else 0


def fetch_all_pages(endpoint: str, service_key: str, params: dict[str, Any]) -> list[dict[str, Any]]:
    first_payload = fetch_api_page(endpoint, params, 1, PAGE_SIZE)
    rows = extract_rows(first_payload, service_key)
    total_count = extract_total_count(first_payload, service_key)
    total_pages = max(1, (total_count + PAGE_SIZE - 1) // PAGE_SIZE)
    if total_pages == 1:
        return rows

    page_numbers = list(range(2, total_pages + 1))
    page_payloads: dict[int, list[dict[str, Any]]] = {}

    with ThreadPoolExecutor(max_workers=PAGE_FETCH_WORKERS) as executor:
        futures = {
            executor.submit(fetch_api_page, endpoint, params, page, PAGE_SIZE): page
            for page in page_numbers
        }
        for future in as_completed(futures):
            page = futures[future]
            payload = future.result()
            page_payloads[page] = extract_rows(payload, service_key)

    for page in page_numbers:
        rows.extend(page_payloads.get(page, []))
    return rows


def normalize_name(value: str | None) -> str:
    return "".join(str(value or "").split())


def extract_representative_name(value: str | None) -> str:
    return str(value or "").split("의원", 1)[0].strip()


def split_history(value: str | None) -> tuple[str | None, list[str]]:
    items = [item.strip() for item in str(value or "").split("/") if item.strip()]
    if not items:
        return None, []
    return items[-1], items[:-1]


def init_db(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        PRAGMA journal_mode = WAL;
        PRAGMA foreign_keys = ON;

        CREATE TABLE IF NOT EXISTS metadata (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS members (
            naas_cd TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            party TEXT,
            district TEXT,
            committee TEXT,
            reelection TEXT,
            elected_terms TEXT,
            gender TEXT,
            phone TEXT,
            email TEXT,
            homepage_url TEXT,
            office_room TEXT,
            photo_url TEXT
        );

        CREATE TABLE IF NOT EXISTS bills (
            bill_id TEXT PRIMARY KEY,
            bill_no TEXT,
            bill_name TEXT NOT NULL,
            ppsr_kind TEXT,
            ppsr_name TEXT,
            proposed_date TEXT,
            committee TEXT,
            result TEXT,
            pass_status TEXT,
            link_url TEXT,
            representative_naas_cd TEXT
        );

        CREATE TABLE IF NOT EXISTS votes (
            bill_id TEXT NOT NULL,
            mona_cd TEXT,
            hg_nm TEXT NOT NULL,
            result_vote_mod TEXT,
            vote_date TEXT,
            bill_name TEXT,
            link_url TEXT,
            PRIMARY KEY (bill_id, hg_nm)
        );

        CREATE TABLE IF NOT EXISTS vote_sync_status (
            bill_id TEXT PRIMARY KEY,
            has_rows INTEGER NOT NULL DEFAULT 0,
            synced_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_bills_rep ON bills(representative_naas_cd);
        CREATE INDEX IF NOT EXISTS idx_votes_name ON votes(hg_nm);
        """
    )
    connection.commit()


def init_result_db(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS dashboard_cache (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            payload_json TEXT NOT NULL,
            generated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS member_detail_cache (
            member_key TEXT PRIMARY KEY,
            payload_json TEXT NOT NULL,
            generated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS member_news_cache (
            member_key TEXT PRIMARY KEY,
            payload_json TEXT NOT NULL,
            fetched_at TEXT NOT NULL
        );
        """
    )
    connection.commit()


def sync_database() -> dict[str, Any]:
    config = load_api_config()
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    init_db(connection)

    update_refresh_progress(
        stage="members",
        message="국회의원 기본 정보를 수집하는 중입니다.",
        progress=8,
        progress_detail="열린국회 의원 목록을 조회하고 있습니다.",
    )
    member_rows = fetch_all_pages(config["member_url"], "ALLNAMEMBER", {})
    current_members = [
        row
        for row in member_rows
        if DEFAULT_ASSEMBLY_LABEL in str(row.get("GTELT_ERACO", ""))
        and row.get("DTY_NM")
    ]

    update_refresh_progress(
        stage="bills",
        message="의안 정보를 수집하는 중입니다.",
        progress=20,
        progress_detail=f"현역 의원 {len(current_members)}명을 기준으로 의안 목록을 정리하고 있습니다.",
    )
    raw_bill_rows = fetch_all_pages(
        config["bill_url"],
        "ALLBILLV2",
        {"ERACO": DEFAULT_ASSEMBLY_LABEL},
    )
    bill_rows = dedupe_rows_by_key(raw_bill_rows, "BILL_ID")

    member_by_name = {
        normalize_name(member.get("NAAS_NM")): member for member in current_members
    }
    vote_bill_ids = sorted({
        bill["BILL_ID"]
        for bill in bill_rows
        if bill.get("BILL_ID") and (bill.get("RGS_RSLN_DT") or bill.get("RGS_CONF_RSLT"))
    })

    existing_vote_bill_ids = {
        row["bill_id"]
        for row in connection.execute("SELECT bill_id FROM vote_sync_status").fetchall()
    }
    pending_vote_bill_ids = [bill_id for bill_id in vote_bill_ids if bill_id not in existing_vote_bill_ids]

    vote_rows_by_bill: dict[str, list[dict[str, Any]]] = {}
    failed_vote_bill_ids: list[str] = []

    update_refresh_progress(
        stage="votes",
        message="표결 정보를 수집하는 중입니다.",
        progress=35,
        progress_detail=(
            f"전체 표결 대상 의안 {len(vote_bill_ids)}건 중 "
            f"새로 수집할 {len(pending_vote_bill_ids)}건을 처리합니다."
        ),
    )

    def fetch_vote_rows(bill_id: str) -> tuple[str, list[dict[str, Any]], str | None]:
        last_error: str | None = None
        for attempt in range(1, VOTE_API_RETRIES + 2):
            try:
                payload = fetch_api_page(
                    config["vote_url"],
                    {"AGE": DEFAULT_ASSEMBLY_NUMBER, "BILL_ID": bill_id},
                    1,
                    PAGE_SIZE,
                    timeout=VOTE_API_TIMEOUT,
                )
                return bill_id, extract_rows(payload, "nojepdqqaweusdfbi"), None
            except Exception as error:
                last_error = str(error)
                if attempt <= VOTE_API_RETRIES:
                    time.sleep(min(2 * attempt, 5))
        return bill_id, [], last_error

    with ThreadPoolExecutor(max_workers=VOTE_WORKERS) as executor:
        futures = [executor.submit(fetch_vote_rows, bill_id) for bill_id in pending_vote_bill_ids]
        completed_vote_jobs = 0
        total_vote_jobs = len(pending_vote_bill_ids)
        for future in as_completed(futures):
            bill_id, rows, error = future.result()
            if error:
                failed_vote_bill_ids.append(bill_id)
            else:
                vote_rows_by_bill[bill_id] = rows
            completed_vote_jobs += 1
            progress_value = 35
            if total_vote_jobs:
                progress_value = 35 + int((completed_vote_jobs / total_vote_jobs) * 35)
            update_refresh_progress(
                stage="votes",
                message="표결 정보를 수집하는 중입니다.",
                progress=progress_value,
                progress_detail=(
                    f"표결 의안 {completed_vote_jobs}/{total_vote_jobs}건 처리 완료, "
                    f"새 표결 행 {sum(len(items) for items in vote_rows_by_bill.values())}건 수집"
                    f"{f', 재시도 후 보류 {len(failed_vote_bill_ids)}건' if failed_vote_bill_ids else ''}"
                ),
            )

    update_refresh_progress(
        stage="database",
        message="수집한 데이터를 데이터베이스에 저장하는 중입니다.",
        progress=74,
        progress_detail=(
            f"의원 {len(current_members)}명, 의안 {len(bill_rows)}건, "
            f"신규 표결 의안 {len(vote_rows_by_bill)}건을 저장합니다."
            f"{f' 보류 {len(failed_vote_bill_ids)}건은 다음 동기화에 다시 시도합니다.' if failed_vote_bill_ids else ''}"
        ),
    )
    with connection:
        connection.executemany(
            """
            INSERT OR REPLACE INTO members (
                naas_cd, name, party, district, committee, reelection, elected_terms,
                gender, phone, email, homepage_url, office_room, photo_url
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    row.get("NAAS_CD"),
                    row.get("NAAS_NM"),
                    row.get("PLPT_NM"),
                    row.get("ELECD_NM"),
                    row.get("BLNG_CMIT_NM") or row.get("CMIT_NM"),
                    row.get("RLCT_DIV_NM"),
                    row.get("GTELT_ERACO"),
                    row.get("NTR_DIV"),
                    row.get("NAAS_TEL_NO"),
                    row.get("NAAS_EMAIL_ADDR"),
                    row.get("NAAS_HP_URL"),
                    row.get("OFFM_RNUM_NO"),
                    row.get("NAAS_PIC"),
                )
                for row in current_members
            ],
        )

        connection.executemany(
            """
            INSERT OR REPLACE INTO bills (
                bill_id, bill_no, bill_name, ppsr_kind, ppsr_name, proposed_date,
                committee, result, pass_status, link_url, representative_naas_cd
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    row.get("BILL_ID"),
                    row.get("BILL_NO"),
                    row.get("BILL_NM"),
                    row.get("PPSR_KND"),
                    row.get("PPSR_NM"),
                    row.get("PPSL_DT"),
                    row.get("JRCMIT_NM"),
                    row.get("RGS_CONF_RSLT"),
                    row.get("PASSGUBN"),
                    row.get("LINK_URL"),
                    (
                        member_by_name.get(
                            normalize_name(extract_representative_name(row.get("PPSR_NM")))
                        ) or {}
                    ).get("NAAS_CD"),
                )
                for row in bill_rows
            ],
        )

        prune_stale_rows(
            connection,
            "members",
            "naas_cd",
            [row.get("NAAS_CD") for row in current_members if row.get("NAAS_CD")],
        )
        prune_stale_rows(
            connection,
            "bills",
            "bill_id",
            [row.get("BILL_ID") for row in bill_rows if row.get("BILL_ID")],
        )
        connection.execute(
            "DELETE FROM votes WHERE bill_id NOT IN (SELECT bill_id FROM bills)"
        )
        connection.execute(
            "DELETE FROM vote_sync_status WHERE bill_id NOT IN (SELECT bill_id FROM bills)"
        )

        vote_records: list[tuple[Any, ...]] = []
        for bill_id, rows in vote_rows_by_bill.items():
            for row in rows:
                vote_records.append(
                    (
                        bill_id,
                        row.get("MONA_CD"),
                        row.get("HG_NM"),
                        row.get("RESULT_VOTE_MOD"),
                        row.get("VOTE_DATE"),
                        row.get("BILL_NAME"),
                        row.get("BILL_URL") or row.get("BILL_NAME_URL"),
                    )
                )

        connection.executemany(
            """
            INSERT OR REPLACE INTO votes (
                bill_id, mona_cd, hg_nm, result_vote_mod, vote_date, bill_name, link_url
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            vote_records,
        )

        synced_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        connection.executemany(
            """
            INSERT OR REPLACE INTO vote_sync_status (bill_id, has_rows, synced_at)
            VALUES (?, ?, ?)
            """,
            [
                (bill_id, 1 if vote_rows_by_bill.get(bill_id) else 0, synced_at)
                for bill_id in pending_vote_bill_ids
                if bill_id not in failed_vote_bill_ids
            ],
        )

        vote_row_count = connection.execute("SELECT COUNT(*) AS count FROM votes").fetchone()["count"]
        metadata = [
            ("assembly_label", DEFAULT_ASSEMBLY_LABEL),
            ("last_synced_at", datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
            ("member_count", str(len(current_members))),
            ("bill_count", str(len(bill_rows))),
            ("vote_row_count", str(vote_row_count)),
            ("new_vote_bill_count", str(len(pending_vote_bill_ids))),
            ("failed_vote_bill_count", str(len(failed_vote_bill_ids))),
            ("current_member_source_type", "ALLNAMEMBER.DTY_NM"),
            ("page_fetch_workers", str(PAGE_FETCH_WORKERS)),
            ("vote_fetch_workers", str(VOTE_WORKERS)),
        ]
        connection.executemany(
            "INSERT OR REPLACE INTO metadata(key, value) VALUES (?, ?)",
            metadata,
        )

    connection.close()
    update_refresh_progress(
        stage="ranking",
        message="랭킹 결과 데이터베이스를 생성하는 중입니다.",
        progress=90,
        progress_detail="저장된 원본 데이터를 바탕으로 결과 DB를 갱신하고 있습니다.",
    )
    payload, member_details = build_dashboard_bundle_from_source()
    save_dashboard_payload_to_result_db(payload, member_details)
    update_refresh_progress(
        stage="ranking",
        message="랭킹 결과 데이터베이스를 생성하는 중입니다.",
        progress=97,
        progress_detail="결과 DB 저장이 거의 완료되었습니다.",
    )
    return payload


def dedupe_rows_by_key(rows: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    deduped: dict[str, dict[str, Any]] = {}
    for row in rows:
        value = row.get(key)
        if value:
            deduped[str(value)] = row
    return list(deduped.values())


def prune_stale_rows(
    connection: sqlite3.Connection,
    table_name: str,
    key_name: str,
    valid_ids: list[str],
) -> None:
    if not valid_ids:
        connection.execute(f"DELETE FROM {table_name}")
        return
    placeholders = ", ".join("?" for _ in valid_ids)
    connection.execute(
        f"DELETE FROM {table_name} WHERE {key_name} NOT IN ({placeholders})",
        valid_ids,
    )


def build_member_detail_payload_from_row(
    connection: sqlite3.Connection,
    member_key: str,
    member_name: str,
) -> dict[str, Any]:
    return {
        "key": member_key,
        "latest_proposals": latest_proposals(connection, member_key),
        "latest_votes": latest_votes(connection, member_name),
    }


def naver_news_is_configured() -> bool:
    return bool(NAVER_CLIENT_ID and NAVER_CLIENT_SECRET)


def strip_html_tags(value: str | None) -> str:
    text = html.unescape(str(value or ""))
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def fetch_naver_news_page(query: str, start: int, display: int = 100) -> list[dict[str, Any]]:
    encoded_query = urllib.parse.quote(query)
    url = (
        "https://openapi.naver.com/v1/search/news.json"
        f"?query={encoded_query}&display={display}&start={start}&sort=date"
    )
    request = urllib.request.Request(
        url,
        headers={
            "X-Naver-Client-Id": NAVER_CLIENT_ID,
            "X-Naver-Client-Secret": NAVER_CLIENT_SECRET,
            "User-Agent": "assembly-ranking/1.0",
        },
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        payload = json.loads(response.read().decode("utf-8"))
    return payload.get("items", [])


def district_base_tokens_v2(district_text: str | None) -> set[str]:
    text = str(district_text or "")
    tokens = set(re.findall(r"[가-힣A-Za-z]{2,}", text))
    reduced = set()
    for token in tokens:
        reduced.add(token)
        reduced.add(re.sub(r"(특별시|광역시|특별자치시|특별자치도|자치구|자치시|자치도|시|군|구|동|읍|면|갑|을|병|정)$", "", token))
    return {token for token in reduced if len(token) >= 2}


def build_member_specific_stopwords_v2(
    member_name: str,
    party_text: str | None = None,
    district_text: str | None = None,
) -> set[str]:
    stopwords = {
        "국회의원", "국회", "의원", "뉴스", "기사", "정치", "정당", "여당", "야당",
        "논평", "대표", "발언", "회의", "참석", "관련", "발표", "입장", "위원회", "위원",
        "브리핑", "속보", "단독", "정부", "질문", "답변", "처리", "법안", "발의", "표결",
        "통과", "심사", "오늘", "내일", "어제", "이번", "최근", "지난", "현장", "기자",
        "보도", "정치권", "정국", "논의", "추진", "검토", "논란", "후보", "인사",
        "서울", "부산", "대구", "인천", "광주", "대전", "울산", "세종", "경기", "강원",
        "충북", "충남", "전북", "전남", "경북", "경남", "제주",
    }
    stopwords.update(part for part in re.split(r"\s+", member_name) if part)
    stopwords.add(normalize_name(member_name))

    for value in (party_text, district_text):
        for token in re.findall(r"[가-힣A-Za-z]{2,}", str(value or "")):
            stopwords.add(token)
            stopwords.add(token.replace(" ", ""))

    stopwords.update(district_base_tokens_v2(district_text))
    return {word for word in stopwords if word}


def tokenize_keywords_v2(
    text: str,
    member_name: str,
    party_text: str | None = None,
    district_text: str | None = None,
) -> list[str]:
    stopwords = build_member_specific_stopwords_v2(member_name, party_text, district_text)
    tokens = re.findall(r"[가-힣A-Za-z]{2,}", text)
    return [
        token for token in tokens
        if token not in stopwords and normalize_name(token) not in stopwords
    ]


def build_monthly_news_keywords_v2(
    member_name: str,
    party_text: str | None = None,
    district_text: str | None = None,
) -> dict[str, Any]:
    try:
        from keyword_pipeline import analyze_documents
        from stopwords import MemberContext, StopwordRegistry
    except ImportError as error:
        return {
            "available": False,
            "configured": naver_news_is_configured(),
            "version": NEWS_KEYWORD_CACHE_VERSION,
            "message": f"뉴스 키워드 분석 모듈을 불러오지 못했습니다: {error}",
            "months": [],
        }

    if not naver_news_is_configured():
        return {
            "available": False,
            "configured": False,
            "version": NEWS_KEYWORD_CACHE_VERSION,
            "message": "네이버 뉴스 API가 아직 설정되지 않았습니다.",
            "months": [],
        }

    today = datetime.now()
    cutoff_date = today - timedelta(days=365)
    query = f"{member_name} 국회의원"
    articles: list[dict[str, Any]] = []

    for start in range(1, 1001, 100):
        items = fetch_naver_news_page(query, start)
        if not items:
            break

        reached_cutoff = False
        for item in items:
            pub_date_raw = str(item.get("pubDate", "")).strip()
            try:
                published_at = datetime.strptime(pub_date_raw, "%a, %d %b %Y %H:%M:%S %z").replace(tzinfo=None)
            except ValueError:
                continue

            if published_at < cutoff_date:
                reached_cutoff = True
                continue

            articles.append(
                {
                    "month": published_at.strftime("%Y-%m"),
                    "title": strip_html_tags(item.get("title")),
                    "description": strip_html_tags(item.get("description")),
                }
            )

        if reached_cutoff:
            break

    monthly_bucket: dict[str, list[str]] = {}
    for article in articles:
        month_bucket = monthly_bucket.setdefault(
            article["month"],
            [],
        )
        month_bucket.append(f'{article["title"]} {article["description"]}'.strip())

    registry = StopwordRegistry()
    member_context = MemberContext(
        member_name=member_name,
        party_name=party_text or "",
        district_name=district_text or "",
        aliases=[f"{member_name} 의원", f"{member_name} 국회의원"],
        related_regions=list(district_base_tokens_v2(district_text)),
    )
    months = []
    for month in sorted(monthly_bucket.keys(), reverse=True):
        analysis_result = analyze_documents(
            monthly_bucket[month],
            registry=registry,
            member_context=member_context,
        )
        top_keywords = analysis_result.top_terms[:20]
        months.append(
            {
                "month": month,
                "article_count": len(monthly_bucket[month]),
                "keywords": [
                    {"keyword": keyword, "count": count}
                    for keyword, count in top_keywords
                ],
            }
        )

    return {
        "available": True,
        "configured": True,
        "version": NEWS_KEYWORD_CACHE_VERSION,
        "message": None,
        "months": months,
    }


def tokenize_keywords(text: str, member_name: str) -> list[str]:
    stopwords = {
        "국회의원", "의원", "뉴스", "기사", "대표", "위원회", "위원", "정부", "여당", "야당",
        "정당", "정치", "속보", "단독", "브리핑", "논란", "관련", "대한", "이번", "오늘",
        "내일", "어제", "지난", "최근", "발표", "회의", "질문", "답변", "처리", "법안",
        "발의", "표결", "통과", "심사", "국회", "한국", "서울",
    }
    stopwords.update(part for part in re.split(r"\s+", member_name) if part)
    tokens = re.findall(r"[가-힣A-Za-z]{2,}", text)
    return [token for token in tokens if token not in stopwords]


def build_monthly_news_keywords(member_name: str) -> dict[str, Any]:
    if not naver_news_is_configured():
        return {
            "available": False,
            "configured": False,
            "message": "네이버 뉴스 API가 아직 설정되지 않았습니다.",
            "months": [],
        }

    today = datetime.now()
    cutoff_date = today - timedelta(days=365)
    query = f"{member_name} 국회의원"
    articles: list[dict[str, Any]] = []

    for start in range(1, 1001, 100):
        items = fetch_naver_news_page(query, start)
        if not items:
            break

        reached_cutoff = False
        for item in items:
            pub_date_raw = str(item.get("pubDate", "")).strip()
            try:
                published_at = datetime.strptime(pub_date_raw, "%a, %d %b %Y %H:%M:%S %z").replace(tzinfo=None)
            except ValueError:
                continue

            if published_at < cutoff_date:
                reached_cutoff = True
                continue

            articles.append(
                {
                    "month": published_at.strftime("%Y-%m"),
                    "title": strip_html_tags(item.get("title")),
                    "description": strip_html_tags(item.get("description")),
                }
            )

        if reached_cutoff:
            break

    monthly_bucket: dict[str, dict[str, Any]] = {}
    for article in articles:
        month_bucket = monthly_bucket.setdefault(
            article["month"],
            {"month": article["month"], "article_count": 0, "keyword_counts": {}},
        )
        month_bucket["article_count"] += 1
        combined_text = f'{article["title"]} {article["description"]}'.strip()
        for keyword in tokenize_keywords(combined_text, member_name):
            month_bucket["keyword_counts"][keyword] = month_bucket["keyword_counts"].get(keyword, 0) + 1

    months = []
    for month in sorted(monthly_bucket.keys(), reverse=True):
        keyword_counts = monthly_bucket[month]["keyword_counts"]
        top_keywords = sorted(
            keyword_counts.items(),
            key=lambda item: (-item[1], item[0]),
        )[:6]
        months.append(
            {
                "month": month,
                "article_count": monthly_bucket[month]["article_count"],
                "keywords": [
                    {"keyword": keyword, "count": count}
                    for keyword, count in top_keywords
                ],
            }
        )

    return {
        "available": True,
        "configured": True,
        "message": None,
        "months": months,
    }


def get_member_news_keywords(
    member_key: str,
    member_name: str,
    party_text: str | None = None,
    district_text: str | None = None,
) -> dict[str, Any]:
    result_connection = sqlite3.connect(RESULT_DB_PATH)
    result_connection.row_factory = sqlite3.Row
    init_result_db(result_connection)
    cached = result_connection.execute(
        "SELECT payload_json, fetched_at FROM member_news_cache WHERE member_key = ?",
        (member_key,),
    ).fetchone()
    cache_cutoff = datetime.now() - timedelta(hours=max(1, NAVER_NEWS_CACHE_HOURS))

    if cached:
        try:
            fetched_at = datetime.fromisoformat(cached["fetched_at"])
            cached_payload = json.loads(cached["payload_json"])
            cached_message = str(cached_payload.get("message") or "")
            has_legacy_import_error = "kiwipiepy" in cached_message
            has_stale_unconfigured_state = (
                cached_payload.get("configured") is False
                and naver_news_is_configured()
            )
            if (
                fetched_at >= cache_cutoff
                and cached_payload.get("version") == NEWS_KEYWORD_CACHE_VERSION
                and not has_legacy_import_error
                and not has_stale_unconfigured_state
            ):
                result_connection.close()
                return cached_payload
        except Exception:
            pass

    try:
        payload = build_monthly_news_keywords_v2(member_name, party_text, district_text)
    except Exception as error:
        if cached:
            result_connection.close()
            stale_payload = json.loads(cached["payload_json"])
            stale_payload["message"] = stale_payload.get("message") or "저장된 뉴스 키워드를 표시하고 있습니다."
            stale_payload["stale"] = True
            return stale_payload

        result_connection.close()
        return {
            "available": False,
            "configured": naver_news_is_configured(),
            "message": f"뉴스 키워드를 불러오지 못했습니다: {error}",
            "months": [],
        }

    result_connection.execute(
        """
        INSERT OR REPLACE INTO member_news_cache (member_key, payload_json, fetched_at)
        VALUES (?, ?, ?)
        """,
        (member_key, json.dumps(payload, ensure_ascii=False), datetime.now().isoformat(timespec="seconds")),
    )
    result_connection.commit()
    result_connection.close()
    return payload


def build_dashboard_bundle_from_source() -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    init_db(connection)

    member_count = connection.execute("SELECT COUNT(*) AS count FROM members").fetchone()["count"]
    if member_count == 0:
        connection.close()
        raise FileNotFoundError("저장된 데이터가 없습니다. 먼저 DB 동기화를 실행해 주세요.")

    metadata = {
        row["key"]: row["value"]
        for row in connection.execute("SELECT key, value FROM metadata").fetchall()
    }
    members = connection.execute("SELECT * FROM members ORDER BY name").fetchall()
    proposal_rows = connection.execute(
        """
        SELECT representative_naas_cd AS naas_cd,
               COUNT(*) AS proposal_count,
               SUM(CASE WHEN pass_status = '처리의안' THEN 1 ELSE 0 END) AS processed_proposal_count
        FROM bills
        WHERE representative_naas_cd IS NOT NULL AND ppsr_kind = '의원'
        GROUP BY representative_naas_cd
        """
    ).fetchall()
    proposal_map = {
        row["naas_cd"]: {
            "proposal_count": row["proposal_count"],
            "processed_proposal_count": row["processed_proposal_count"] or 0,
        }
        for row in proposal_rows
    }

    vote_rows = connection.execute(
        """
        SELECT m.naas_cd,
               COUNT(v.bill_id) AS total_vote_count,
               SUM(CASE WHEN v.result_vote_mod IN ('찬성', '반대', '기권') THEN 1 ELSE 0 END) AS attended_vote_count,
               SUM(CASE WHEN v.result_vote_mod = '찬성' THEN 1 ELSE 0 END) AS yes_count,
               SUM(CASE WHEN v.result_vote_mod = '반대' THEN 1 ELSE 0 END) AS no_count,
               SUM(CASE WHEN v.result_vote_mod = '기권' THEN 1 ELSE 0 END) AS abstain_count,
               SUM(CASE WHEN v.result_vote_mod NOT IN ('찬성', '반대', '기권') THEN 1 ELSE 0 END) AS absent_count
        FROM members m
        LEFT JOIN votes v ON REPLACE(v.hg_nm, ' ', '') = REPLACE(m.name, ' ', '')
        GROUP BY m.naas_cd
        """
    ).fetchall()
    vote_map = {row["naas_cd"]: dict(row) for row in vote_rows}

    max_proposal = max((proposal_map.get(member["naas_cd"], {}).get("proposal_count", 0) for member in members), default=0)
    max_processed = max((proposal_map.get(member["naas_cd"], {}).get("processed_proposal_count", 0) for member in members), default=0)

    rankings: list[dict[str, Any]] = []
    member_details: dict[str, dict[str, Any]] = {}
    for member in members:
        proposals = proposal_map.get(member["naas_cd"], {})
        votes = vote_map.get(member["naas_cd"], {})
        total_vote_count = int(votes.get("total_vote_count", 0) or 0)
        attended_vote_count = int(votes.get("attended_vote_count", 0) or 0)
        attendance_rate = (attended_vote_count / total_vote_count * 100) if total_vote_count else 0
        proposal_count = int(proposals.get("proposal_count", 0) or 0)
        processed_proposal_count = int(proposals.get("processed_proposal_count", 0) or 0)
        proposal_score = (proposal_count / max_proposal * 100) if max_proposal else 0
        processed_score = (processed_proposal_count / max_processed * 100) if max_processed else 0
        score = (attendance_rate * 0.65) + (proposal_score * 0.25) + (processed_score * 0.1)
        member_details[member["naas_cd"]] = build_member_detail_payload_from_row(
            connection,
            member["naas_cd"],
            member["name"],
        )
        rankings.append(
            {
                "key": member["naas_cd"],
                "name": member["name"],
                "party": member["party"],
                "current_party": split_history(member["party"])[0],
                "party_history": split_history(member["party"])[1],
                "district": member["district"],
                "current_district": split_history(member["district"])[0],
                "district_history": split_history(member["district"])[1],
                "committee": member["committee"],
                "reelection": member["reelection"],
                "photo_url": member["photo_url"],
                "phone": member["phone"],
                "email": member["email"],
                "homepage_url": member["homepage_url"],
                "attendance_rate": round(attendance_rate, 2),
                "attended_vote_count": attended_vote_count,
                "total_vote_count": total_vote_count,
                "proposal_count": proposal_count,
                "processed_proposal_count": processed_proposal_count,
                "yes_count": int(votes.get("yes_count", 0) or 0),
                "no_count": int(votes.get("no_count", 0) or 0),
                "abstain_count": int(votes.get("abstain_count", 0) or 0),
                "absent_count": int(votes.get("absent_count", 0) or 0),
                "score": round(score, 2),
            }
        )

    rankings.sort(key=lambda item: (-item["score"], -item["attendance_rate"], item["name"]))
    for index, item in enumerate(rankings, start=1):
        item["rank"] = index

    avg_attendance = sum(item["attendance_rate"] for item in rankings) / len(rankings) if rankings else 0
    top_proposer = max(rankings, key=lambda item: item["proposal_count"], default=None)
    summary = {
        "member_count": len(rankings),
        "average_attendance_rate": round(avg_attendance, 2),
        "total_proposals": sum(item["proposal_count"] for item in rankings),
        "top_proposer_name": top_proposer["name"] if top_proposer else None,
        "top_proposal_count": top_proposer["proposal_count"] if top_proposer else 0,
    }
    meta = {
        "assembly_label": metadata.get("assembly_label", DEFAULT_ASSEMBLY_LABEL),
        "last_synced_at": metadata.get("last_synced_at"),
        "member_count": int(metadata.get("member_count", len(rankings))),
        "bill_count": int(metadata.get("bill_count", 0)),
        "vote_row_count": int(metadata.get("vote_row_count", 0)),
        "failed_vote_bill_count": int(metadata.get("failed_vote_bill_count", 0)),
        "current_member_source_type": metadata.get("current_member_source_type"),
        "database_path": str(DB_PATH),
    }
    connection.close()
    return {"meta": meta, "summary": summary, "rankings": rankings}, member_details


def build_dashboard_payload_from_source() -> dict[str, Any]:
    payload, _ = build_dashboard_bundle_from_source()
    return payload


def save_dashboard_payload_to_result_db(
    payload: dict[str, Any],
    member_details: dict[str, dict[str, Any]] | None = None,
) -> None:
    connection = sqlite3.connect(RESULT_DB_PATH)
    init_result_db(connection)
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with connection:
        connection.execute(
            """
            INSERT OR REPLACE INTO dashboard_cache (id, payload_json, generated_at)
            VALUES (1, ?, ?)
            """,
            (json.dumps(payload, ensure_ascii=False), generated_at),
        )
        if member_details is not None:
            connection.execute("DELETE FROM member_detail_cache")
            connection.executemany(
                """
                INSERT OR REPLACE INTO member_detail_cache (member_key, payload_json, generated_at)
                VALUES (?, ?, ?)
                """,
                [
                    (
                        member_key,
                        json.dumps(detail_payload, ensure_ascii=False),
                        generated_at,
                    )
                    for member_key, detail_payload in member_details.items()
                ],
            )
    connection.close()


def validate_result_db_file(path: Path) -> None:
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    try:
        init_result_db(connection)
        cached = connection.execute(
            "SELECT payload_json FROM dashboard_cache WHERE id = 1"
        ).fetchone()
        if not cached:
            raise ValueError("dashboard_cache 데이터가 없습니다.")
        payload = json.loads(cached["payload_json"])
        if "meta" not in payload or "rankings" not in payload:
            raise ValueError("결과 DB payload 형식이 올바르지 않습니다.")
    finally:
        connection.close()


def build_dashboard_payload() -> dict[str, Any]:
    result_connection = sqlite3.connect(RESULT_DB_PATH)
    result_connection.row_factory = sqlite3.Row
    init_result_db(result_connection)
    cached = result_connection.execute(
        "SELECT payload_json FROM dashboard_cache WHERE id = 1"
    ).fetchone()
    result_connection.close()

    if cached:
        return json.loads(cached["payload_json"])

    payload, member_details = build_dashboard_bundle_from_source()
    save_dashboard_payload_to_result_db(payload, member_details)
    return payload


def build_member_detail_payload(member_key: str) -> dict[str, Any]:
    member_name: str | None = None
    party_text: str | None = None
    district_text: str | None = None
    payload: dict[str, Any] | None = None

    result_connection = sqlite3.connect(RESULT_DB_PATH)
    result_connection.row_factory = sqlite3.Row
    init_result_db(result_connection)
    cached = result_connection.execute(
        "SELECT payload_json FROM member_detail_cache WHERE member_key = ?",
        (member_key,),
    ).fetchone()
    dashboard_cached = result_connection.execute(
        "SELECT payload_json FROM dashboard_cache WHERE id = 1"
    ).fetchone()
    result_connection.close()

    matched = None
    if dashboard_cached:
        dashboard_payload = json.loads(dashboard_cached["payload_json"])
        matched = next(
            (
                item for item in dashboard_payload.get("rankings", [])
                if item.get("key") == member_key
            ),
            None,
        )
        if matched:
            member_name = matched.get("name")
            party_text = matched.get("party") or matched.get("current_party")
            district_text = matched.get("district") or matched.get("current_district")

    if cached:
        payload = json.loads(cached["payload_json"])
    elif matched and (
        "latest_proposals" in matched or "latest_votes" in matched
    ):
        payload = {
            "key": member_key,
            "latest_proposals": matched.get("latest_proposals", []),
            "latest_votes": matched.get("latest_votes", []),
        }

    source_connection = sqlite3.connect(DB_PATH)
    source_connection.row_factory = sqlite3.Row
    init_db(source_connection)
    member = source_connection.execute(
        "SELECT naas_cd, name, party, district FROM members WHERE naas_cd = ?",
        (member_key,),
    ).fetchone()
    if member:
        member_name = member["name"]
        party_text = member["party"]
        district_text = member["district"]
    if not member and not payload:
        source_connection.close()
        raise FileNotFoundError("의원 상세 데이터를 찾을 수 없습니다.")

    if payload is None and member:
        payload = build_member_detail_payload_from_row(
            source_connection,
            member["naas_cd"],
            member["name"],
        )
    source_connection.close()

    if payload is None:
        raise FileNotFoundError("의원 상세 데이터를 찾을 수 없습니다.")

    payload["news_keywords"] = get_member_news_keywords(
        member_key,
        member_name or "",
        party_text,
        district_text,
    )
    return payload


def get_refresh_status() -> dict[str, Any]:
    with refresh_job_lock:
        if REFRESH_STATUS_PATH.exists():
            try:
                return {**default_refresh_status(), **json.loads(REFRESH_STATUS_PATH.read_text(encoding="utf-8"))}
            except Exception:
                pass
        return dict(refresh_job_state)


def set_refresh_status(**updates: Any) -> None:
    with refresh_job_lock:
        refresh_job_state.update(updates)
        REFRESH_STATUS_PATH.write_text(
            json.dumps(refresh_job_state, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


def update_refresh_progress(
    *,
    stage: str,
    message: str,
    progress: int,
    progress_detail: str | None = None,
    **extra: Any,
) -> None:
    set_refresh_status(
        stage=stage,
        message=message,
        progress=max(0, min(100, progress)),
        progress_detail=progress_detail,
        **extra,
    )


def run_refresh_job() -> None:
    set_refresh_status(
        status="running",
        stage="preparing",
        message="동기화를 준비하는 중입니다.",
        progress=2,
        progress_detail="초기 설정을 확인하고 있습니다.",
        started_at=datetime.now().isoformat(timespec="seconds"),
        finished_at=None,
        error=None,
    )
    try:
        payload = sync_database()
        deferred_vote_count = payload.get("meta", {}).get("failed_vote_bill_count", 0)
        set_refresh_status(
            status="completed",
            stage="completed",
            message="데이터 동기화가 완료되었습니다.",
            progress=100,
            progress_detail=(
                f"의원 {payload.get('meta', {}).get('member_count', 0)}명, "
                f"의안 {payload.get('meta', {}).get('bill_count', 0)}건, "
                f"표결 {payload.get('meta', {}).get('vote_row_count', 0)}건"
                f"{f', 보류 표결 의안 {deferred_vote_count}건' if deferred_vote_count else ''}"
            ),
            finished_at=datetime.now().isoformat(timespec="seconds"),
            last_synced_at=payload.get("meta", {}).get("last_synced_at"),
            error=None,
        )
    except Exception as error:
        print(f"Background refresh failed: {error}", flush=True)
        traceback.print_exc()
        set_refresh_status(
            status="failed",
            stage="failed",
            message="데이터 동기화 중 오류가 발생했습니다.",
            finished_at=datetime.now().isoformat(timespec="seconds"),
            error=str(error),
        )


def start_refresh_job() -> dict[str, Any]:
    current_status = get_refresh_status()
    if current_status.get("status") == "running":
        return current_status
    set_refresh_status(
        status="queued",
        stage="queued",
        message="동기화 작업을 대기열에 등록했습니다.",
        progress=1,
        progress_detail="잠시 후 수집을 시작합니다.",
        started_at=datetime.now().isoformat(timespec="seconds"),
        finished_at=None,
        error=None,
    )
    threading.Thread(target=run_refresh_job, daemon=True).start()
    return get_refresh_status()


def latest_proposals(connection: sqlite3.Connection, naas_cd: str) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT bill_name, bill_no, proposed_date, COALESCE(result, pass_status) AS result, link_url
        FROM bills
        WHERE representative_naas_cd = ?
        ORDER BY proposed_date DESC, bill_no DESC
        LIMIT 5
        """,
        (naas_cd,),
    ).fetchall()
    return [dict(row) for row in rows]


def latest_votes(connection: sqlite3.Connection, member_name: str) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT bill_name, vote_date, result_vote_mod, link_url
        FROM votes
        WHERE REPLACE(hg_nm, ' ', '') = REPLACE(?, ' ', '')
        ORDER BY vote_date DESC
        LIMIT 5
        """,
        (member_name,),
    ).fetchall()
    return [dict(row) for row in rows]


class AppHandler(SimpleHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, directory=str(BASE_DIR), **kwargs)

    def do_GET(self) -> None:
        try:
            request_path = urllib.parse.urlparse(self.path).path
            if request_path == "/api/dashboard":
                self.handle_dashboard()
                return
            if request_path == "/":
                self.serve_static_file(BASE_DIR / "index.html")
                return
            if request_path == "/favicon.ico":
                self.send_response(HTTPStatus.NO_CONTENT)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return

            file_path = (BASE_DIR / request_path.lstrip("/")).resolve()
            if not str(file_path).startswith(str(BASE_DIR.resolve())):
                self.send_error(HTTPStatus.FORBIDDEN, "Forbidden")
                return
            if file_path.is_file():
                self.serve_static_file(file_path)
                return

            self.send_error(HTTPStatus.NOT_FOUND, "Not found")
        except Exception as error:
            self.log_runtime_exception(error)
            self.respond_json({"error": str(error)}, status=HTTPStatus.INTERNAL_SERVER_ERROR)

    def do_POST(self) -> None:
        try:
            request_path = urllib.parse.urlparse(self.path).path
            if request_path == "/api/refresh":
                self.handle_refresh()
                return
            if request_path == "/api/rebuild-result-db":
                self.handle_rebuild_result_db()
                return
            self.send_error(HTTPStatus.NOT_FOUND, "Not found")
        except Exception as error:
            self.log_runtime_exception(error)
            self.respond_json({"error": str(error)}, status=HTTPStatus.INTERNAL_SERVER_ERROR)

    def handle_dashboard(self) -> None:
        try:
            payload = build_dashboard_payload()
            self.respond_json(payload)
        except FileNotFoundError as error:
            self.respond_json({"error": str(error)}, status=HTTPStatus.NOT_FOUND)
        except Exception as error:
            self.respond_json({"error": str(error)}, status=HTTPStatus.INTERNAL_SERVER_ERROR)

    def handle_refresh(self) -> None:
        try:
            payload = sync_database()
            self.respond_json(payload)
        except Exception as error:
            self.respond_json({"error": str(error)}, status=HTTPStatus.INTERNAL_SERVER_ERROR)

    def handle_rebuild_result_db(self) -> None:
        try:
            payload = build_dashboard_payload_from_source()
            save_dashboard_payload_to_result_db(payload)
            self.respond_json(payload)
        except Exception as error:
            self.respond_json({"error": str(error)}, status=HTTPStatus.INTERNAL_SERVER_ERROR)

    def respond_json(self, payload: dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def serve_static_file(self, file_path: Path) -> None:
        body = file_path.read_bytes()
        content_type = mimetypes.guess_type(str(file_path))[0] or "application/octet-stream"
        if content_type.startswith("text/") or content_type in {"application/javascript", "application/json"}:
            content_type = f"{content_type}; charset=utf-8"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def log_runtime_exception(self, error: Exception) -> None:
        print(f"Request failed: {self.command} {self.path} -> {error}", flush=True)
        traceback.print_exc()


class DualStackThreadingHTTPServer(ThreadingHTTPServer):
    address_family = socket.AF_INET6

    def server_bind(self) -> None:
        if hasattr(socket, "IPPROTO_IPV6") and hasattr(socket, "IPV6_V6ONLY"):
            try:
                self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
            except OSError:
                pass
        super().server_bind()


def create_http_server(host: str, port: int) -> ThreadingHTTPServer:
    normalized_host = host.strip()
    if normalized_host == "0.0.0.0":
        try:
            return DualStackThreadingHTTPServer(("::", port), AppHandler)
        except OSError:
            pass
    return ThreadingHTTPServer((host, port), AppHandler)


app = Flask(__name__, static_folder=None)


def render_index_html() -> str:
    html = (BASE_DIR / "index.html").read_text(encoding="utf-8")
    initial_payload = None
    try:
        initial_payload = build_dashboard_payload()
    except Exception:
        initial_payload = None

    payload_json = json.dumps(initial_payload, ensure_ascii=False).replace("</", "<\\/")
    html = html.replace(
        '<script>window.__INITIAL_DASHBOARD__ = null;</script>',
        f"<script>window.__INITIAL_DASHBOARD__ = {payload_json};</script>",
        1,
    )
    return html


@app.get("/")
def flask_index() -> Response:
    return render_index_html()


@app.get("/member/<member_key>")
def flask_member_page(member_key: str) -> Response:
    return render_index_html()


@app.get("/app.js")
def flask_app_js() -> Response:
    return send_from_directory(BASE_DIR, "app.js", mimetype="application/javascript")


@app.get("/styles.css")
def flask_styles_css() -> Response:
    return send_from_directory(BASE_DIR, "styles.css", mimetype="text/css")


@app.get("/favicon.ico")
def flask_favicon() -> Response:
    return Response(status=HTTPStatus.NO_CONTENT)


@app.get("/api/dashboard")
def flask_dashboard() -> Response:
    try:
        return jsonify(build_dashboard_payload())
    except FileNotFoundError as error:
        return jsonify({"error": str(error)}), HTTPStatus.NOT_FOUND
    except Exception as error:
        print(f"Request failed: GET /api/dashboard -> {error}", flush=True)
        traceback.print_exc()
        return jsonify({"error": str(error)}), HTTPStatus.INTERNAL_SERVER_ERROR


@app.post("/api/refresh")
def flask_refresh() -> Response:
    try:
        return jsonify(start_refresh_job()), HTTPStatus.ACCEPTED
    except Exception as error:
        print(f"Request failed: POST /api/refresh -> {error}", flush=True)
        traceback.print_exc()
        return jsonify({"error": str(error)}), HTTPStatus.INTERNAL_SERVER_ERROR


@app.get("/api/refresh-status")
def flask_refresh_status() -> Response:
    return jsonify(get_refresh_status())


@app.post("/api/rebuild-result-db")
def flask_rebuild_result_db() -> Response:
    try:
        payload, member_details = build_dashboard_bundle_from_source()
        save_dashboard_payload_to_result_db(payload, member_details)
        return jsonify(payload)
    except Exception as error:
        print(f"Request failed: POST /api/rebuild-result-db -> {error}", flush=True)
        traceback.print_exc()
        return jsonify({"error": str(error)}), HTTPStatus.INTERNAL_SERVER_ERROR


@app.get("/api/member-detail/<member_key>")
def flask_member_detail(member_key: str) -> Response:
    try:
        return jsonify(build_member_detail_payload(member_key))
    except FileNotFoundError as error:
        return jsonify({"error": str(error)}), HTTPStatus.NOT_FOUND
    except Exception as error:
        print(f"Request failed: GET /api/member-detail/{member_key} -> {error}", flush=True)
        traceback.print_exc()
        return jsonify({"error": str(error)}), HTTPStatus.INTERNAL_SERVER_ERROR


@app.post("/api/admin/upload-result-db")
def flask_upload_result_db() -> Response:
    if not ADMIN_UPLOAD_TOKEN:
        return jsonify({"error": "ADMIN_UPLOAD_TOKEN 이 설정되지 않았습니다."}), HTTPStatus.FORBIDDEN

    provided_token = request.headers.get("X-Admin-Token", "").strip()
    if provided_token != ADMIN_UPLOAD_TOKEN:
        return jsonify({"error": "업로드 인증에 실패했습니다."}), HTTPStatus.UNAUTHORIZED

    raw_bytes = request.get_data(cache=False)
    if not raw_bytes:
        return jsonify({"error": "업로드할 DB 파일 내용이 비어 있습니다."}), HTTPStatus.BAD_REQUEST

    try:
        RESULT_DB_UPLOAD_PATH.write_bytes(raw_bytes)
        validate_result_db_file(RESULT_DB_UPLOAD_PATH)
        RESULT_DB_UPLOAD_PATH.replace(RESULT_DB_PATH)
        payload = build_dashboard_payload()
        return jsonify(
            {
                "ok": True,
                "message": "결과 DB 업로드가 완료되었습니다.",
                "meta": payload.get("meta", {}),
            }
        )
    except Exception as error:
        if RESULT_DB_UPLOAD_PATH.exists():
            RESULT_DB_UPLOAD_PATH.unlink(missing_ok=True)
        print(f"Result DB upload failed: {error}", flush=True)
        traceback.print_exc()
        return jsonify({"error": str(error)}), HTTPStatus.BAD_REQUEST


@app.get("/<path:asset_path>")
def flask_asset(asset_path: str) -> Response:
    file_path = (BASE_DIR / asset_path).resolve()
    if not str(file_path).startswith(str(BASE_DIR.resolve())) or not file_path.is_file():
        return jsonify({"error": "Not found"}), HTTPStatus.NOT_FOUND
    return send_from_directory(BASE_DIR, asset_path)


def get_local_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            return sock.getsockname()[0]
    except OSError:
        return "127.0.0.1"


def main() -> None:
    parser = argparse.ArgumentParser(description="Serve the assembly ranking app.")
    parser.add_argument("--host", default="0.0.0.0", help="Host to bind. Use 127.0.0.1 for local only.")
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("PORT", "8000")),
        help="Port to listen on.",
    )
    parser.add_argument(
        "--refresh-and-exit",
        action="store_true",
        help="Refresh source data and rebuild result DB, then exit.",
    )
    args = parser.parse_args()

    if args.refresh_and_exit:
        print("Refreshing source DB and rebuilding result DB...")
        payload = sync_database()
        print(
            f"Done. Synced {payload['meta']['member_count']} members, "
            f"{payload['meta']['bill_count']} bills, {payload['meta']['vote_row_count']} votes."
        )
        return

    local_ip = get_local_ip()
    print(f"Serving database-backed app at http://127.0.0.1:{args.port}")
    print(f"Data directory: {DATA_DIR}")
    if args.host == "0.0.0.0":
        print(f"LAN access: http://{local_ip}:{args.port}")
        print("For internet access, open the firewall and port-forward this port on your router or use a tunnel.")
    try:
        app.run(host=args.host, port=args.port, threaded=True)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
