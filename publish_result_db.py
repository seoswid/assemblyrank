from __future__ import annotations

import argparse
import json
import os
import urllib.request
from pathlib import Path

import server


def rebuild_local_result_db() -> Path:
    payload = server.build_dashboard_payload_from_source()
    server.save_dashboard_payload_to_result_db(payload)
    return server.RESULT_DB_PATH


def upload_result_db(result_db_path: Path, base_url: str, token: str) -> dict:
    endpoint = f"{base_url.rstrip('/')}/api/admin/upload-result-db"
    request = urllib.request.Request(
        endpoint,
        data=result_db_path.read_bytes(),
        method="POST",
        headers={
            "Content-Type": "application/octet-stream",
            "X-Admin-Token": token,
        },
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        return json.loads(response.read().decode("utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser(description="Rebuild and upload the result DB to Railway.")
    parser.add_argument(
        "--url",
        default=os.environ.get("RAILWAY_APP_URL", "").strip(),
        help="Deployed app base URL, e.g. https://assemblyrank-production.up.railway.app",
    )
    parser.add_argument(
        "--token",
        default=os.environ.get("ADMIN_UPLOAD_TOKEN", "").strip(),
        help="Admin upload token configured on Railway.",
    )
    parser.add_argument(
        "--skip-rebuild",
        action="store_true",
        help="Upload the current local result DB without rebuilding it first.",
    )
    args = parser.parse_args()

    if not args.url:
        raise SystemExit("--url 또는 RAILWAY_APP_URL 이 필요합니다.")
    if not args.token:
        raise SystemExit("--token 또는 ADMIN_UPLOAD_TOKEN 이 필요합니다.")

    result_db_path = server.RESULT_DB_PATH if args.skip_rebuild else rebuild_local_result_db()
    response = upload_result_db(result_db_path, args.url, args.token)
    print(json.dumps(response, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
