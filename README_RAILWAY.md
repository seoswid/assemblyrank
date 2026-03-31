# Railway 배포 안내

## 1. 업로드
- 이 폴더를 GitHub 저장소로 올립니다.
- Railway에서 `Deploy from GitHub repo`로 연결합니다.

## 2. 볼륨 생성
- Railway 서비스에 Volume을 하나 추가합니다.
- 마운트 경로는 `/data`로 지정합니다.

## 3. 환경 변수
- `DATA_DIR=/data`

## 4. 실행
- `railway.json`이 있으면 Railway가 자동으로
  `python server.py --host 0.0.0.0 --port $PORT`
  로 실행합니다.

## 5. 첫 실행 후
- 배포 URL 접속
- `데이터 업데이트` 클릭
- 원본 DB와 결과 DB가 모두 `/data` 아래에 생성됩니다.

## 6. 자동 업데이트 추천
- Railway 공식 문서 기준으로, 주기 실행은 별도 Cron Job 서비스로 두는 것이 가장 안전합니다.
- 웹 서비스는 계속 켜져 있어야 하고, Cron Job 서비스는 작업 후 종료되어야 합니다.
- Cron 서비스의 시작 명령은 아래처럼 설정합니다.

```bash
python server.py --refresh-and-exit
```

- 예시 스케줄:
  - 매일 한국시간 오전 6시 = UTC 21시 전날
  - Cron 표현식: `0 21 * * *`

- Railway 문서:
  - [Cron Jobs](https://docs.railway.com/guides/cron-jobs)
  - [Services](https://docs.railway.com/deploy/services)

## 7. 저장되는 파일
- `assembly_rankings.db`
- `assembly_rankings_result.db`

## 8. 추천
- 결과가 안정화되면 Railway Volume 백업도 켜 두는 것이 좋습니다.
