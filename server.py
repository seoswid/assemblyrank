from __future__ import annotations

import argparse
import json
import mimetypes
import os
import sqlite3
import socket
import traceback
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(
    os.environ.get("DATA_DIR")
    or os.environ.get("RAILWAY_VOLUME_MOUNT_PATH")
    or BASE_DIR
).resolve()
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "assembly_rankings.db"
RESULT_DB_PATH = DATA_DIR / "assembly_rankings_result.db"
API_URL_PATH = BASE_DIR / "API_URL.txt"
DEFAULT_ASSEMBLY_NUMBER = 22
DEFAULT_ASSEMBLY_LABEL = f"제{DEFAULT_ASSEMBLY_NUMBER}대"
PAGE_SIZE = 1000
VOTE_WORKERS = 4
PAGE_FETCH_WORKERS = 6
ATTENDED_RESULTS = {"찬성", "반대", "기권"}
FALLBACK_API_CONFIG = {
    "key": "fc8d86af691f4f5798f7fe39595d1de9",
    "member_url": "https://open.assembly.go.kr/portal/openapi/ALLNAMEMBER",
    "bill_url": "https://open.assembly.go.kr/portal/openapi/ALLBILLV2",
    "vote_url": "https://open.assembly.go.kr/portal/openapi/nojepdqqaweusdfbi",
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


def fetch_api_page(endpoint: str, params: dict[str, Any], page_index: int, page_size: int) -> dict[str, Any]:
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
    with urllib.request.urlopen(request, timeout=60) as response:
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
        """
    )
    connection.commit()


def sync_database() -> dict[str, Any]:
    config = load_api_config()
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    init_db(connection)

    member_rows = fetch_all_pages(config["member_url"], "ALLNAMEMBER", {})
    current_members = [
        row
        for row in member_rows
        if DEFAULT_ASSEMBLY_LABEL in str(row.get("GTELT_ERACO", ""))
        and row.get("DTY_NM")
    ]

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

    def fetch_vote_rows(bill_id: str) -> tuple[str, list[dict[str, Any]]]:
        payload = fetch_api_page(
            config["vote_url"],
            {"AGE": DEFAULT_ASSEMBLY_NUMBER, "BILL_ID": bill_id},
            1,
            PAGE_SIZE,
        )
        return bill_id, extract_rows(payload, "nojepdqqaweusdfbi")

    with ThreadPoolExecutor(max_workers=VOTE_WORKERS) as executor:
        futures = [executor.submit(fetch_vote_rows, bill_id) for bill_id in pending_vote_bill_ids]
        for future in as_completed(futures):
            bill_id, rows = future.result()
            vote_rows_by_bill[bill_id] = rows

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
            ("current_member_source_type", "ALLNAMEMBER.DTY_NM"),
            ("page_fetch_workers", str(PAGE_FETCH_WORKERS)),
            ("vote_fetch_workers", str(VOTE_WORKERS)),
        ]
        connection.executemany(
            "INSERT OR REPLACE INTO metadata(key, value) VALUES (?, ?)",
            metadata,
        )

    connection.close()
    payload = build_dashboard_payload_from_source()
    save_dashboard_payload_to_result_db(payload)
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


def build_dashboard_payload_from_source() -> dict[str, Any]:
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
                "latest_proposals": latest_proposals(connection, member["naas_cd"]),
                "latest_votes": latest_votes(connection, member["name"]),
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
        "current_member_source_type": metadata.get("current_member_source_type"),
        "database_path": str(DB_PATH),
    }
    connection.close()
    return {"meta": meta, "summary": summary, "rankings": rankings}


def save_dashboard_payload_to_result_db(payload: dict[str, Any]) -> None:
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

    payload = build_dashboard_payload_from_source()
    save_dashboard_payload_to_result_db(payload)
    return payload


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
        self.end_headers()
        self.wfile.write(body)

    def log_runtime_exception(self, error: Exception) -> None:
        print(f"Request failed: {self.command} {self.path} -> {error}", flush=True)
        traceback.print_exc()


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

    server = ThreadingHTTPServer((args.host, args.port), AppHandler)
    local_ip = get_local_ip()
    print(f"Serving database-backed app at http://127.0.0.1:{args.port}")
    print(f"Data directory: {DATA_DIR}")
    if args.host == "0.0.0.0":
        print(f"LAN access: http://{local_ip}:{args.port}")
        print("For internet access, open the firewall and port-forward this port on your router or use a tunnel.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
