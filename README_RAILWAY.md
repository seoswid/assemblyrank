# Railway 배포 메모

## 기본 설정
- GitHub 저장소를 Railway에 연결합니다.
- Volume 마운트 경로를 `/data` 로 설정합니다.
- 환경 변수 `DATA_DIR=/data` 를 추가합니다.
- 환경 변수 `ADMIN_UPLOAD_TOKEN=<임의의 긴 비밀값>` 을 추가합니다.

## 웹 서비스 실행
- Railway는 [railway.json](/C:/Users/oragu/Desktop/watch/railway.json) 또는 [Procfile](/C:/Users/oragu/Desktop/watch/Procfile)에 따라 `gunicorn`으로 실행됩니다.

## 추천 운영 방식
- 로컬 PC에서 원본 DB를 업데이트합니다.
- 로컬 PC에서 결과 DB `assembly_rankings_result.db` 를 다시 만듭니다.
- [publish_result_db.py](/C:/Users/oragu/Desktop/watch/publish_result_db.py) 로 Railway에 결과 DB만 업로드합니다.

## 로컬에서 결과 DB 업로드
```powershell
cd C:\Users\oragu\Desktop\watch
python publish_result_db.py --url https://assemblyrank-production.up.railway.app --token YOUR_ADMIN_UPLOAD_TOKEN
```

## 업로드만 다시 실행
```powershell
cd C:\Users\oragu\Desktop\watch
python publish_result_db.py --skip-rebuild --url https://assemblyrank-production.up.railway.app --token YOUR_ADMIN_UPLOAD_TOKEN
```

## 서버 측 업로드 API
- 엔드포인트: `/api/admin/upload-result-db`
- 헤더: `X-Admin-Token: <ADMIN_UPLOAD_TOKEN>`
- 본문: `assembly_rankings_result.db` 바이너리 그대로 전송

## 장점
- Railway에서 느린 열린국회 API 전체 동기화를 돌리지 않아도 됩니다.
- 결과 DB만 올리므로 배포가 빠르고 안정적입니다.
- 원본 DB가 커도 GitHub에 올릴 필요가 없습니다.
