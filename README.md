# assemblyrank

제22대 국회의원 활동 랭킹을 보여주는 웹 애플리케이션입니다.

- 백엔드: Python, Flask, SQLite
- 프런트엔드: HTML, CSS, JavaScript
- 데이터 소스: 열린국회 오픈API, 네이버 뉴스 API(선택)

## 빠른 시작

### 1. 저장소 받기

```powershell
git clone https://github.com/seoswid/assemblyrank.git
cd assemblyrank
```

### 2. 의존성 설치

```powershell
python -m pip install -r requirements.txt
```

### 3. 로컬 서버 실행

```powershell
python server.py
```

브라우저에서 아래 주소로 접속합니다.

- [http://127.0.0.1:8000](http://127.0.0.1:8000)

## 뉴스 키워드 기능 사용

네이버 뉴스 키워드 기능을 쓰려면 실행 전에 환경변수를 넣어야 합니다.

### PowerShell

```powershell
$env:NAVER_CLIENT_ID="YOUR_CLIENT_ID"
$env:NAVER_CLIENT_SECRET="YOUR_CLIENT_SECRET"
python server.py
```

### CMD

```cmd
set NAVER_CLIENT_ID=YOUR_CLIENT_ID
set NAVER_CLIENT_SECRET=YOUR_CLIENT_SECRET
python server.py
```

## 어디서든 작업하는 방법

### 1. GitHub 웹에서 바로 수정

- GitHub 저장소에서 파일 열기
- 연필 아이콘으로 수정
- 바로 커밋 가능

간단한 문구 수정이나 CSS 수정에 적합합니다.

### 2. 다른 PC에서 로컬 작업

```powershell
git clone https://github.com/seoswid/assemblyrank.git
cd assemblyrank
python -m pip install -r requirements.txt
python server.py
```

### 3. GitHub Codespaces 사용

이 저장소에는 `.devcontainer/devcontainer.json`이 포함되어 있어서 Codespaces에서 바로 열 수 있습니다.

권장 흐름:

1. GitHub 저장소 페이지 열기
2. `Code`
3. `Codespaces`
4. `Create codespace on main`

Codespaces가 열리면 의존성이 자동 설치됩니다.

## 데이터 운영 방식

이 프로젝트는 큰 원본 DB를 GitHub에 올리지 않습니다.

- 원본 DB: `assembly_rankings.db`
- 결과 DB: `assembly_rankings_result.db`

권장 운영:

1. 로컬에서 데이터 갱신
2. 결과 DB만 서버에 업로드

업로드 스크립트:

```powershell
python publish_result_db.py --skip-rebuild --url https://assemblyrank-production.up.railway.app --token YOUR_ADMIN_UPLOAD_TOKEN
```

## Railway 배포

Railway 관련 배포 방법은 아래 문서를 참고합니다.

- [README_RAILWAY.md](C:\Users\oragu\Desktop\watch\README_RAILWAY.md)

## 자주 쓰는 명령

### 문법 확인

```powershell
python -m py_compile server.py stopwords.py keyword_pipeline.py demo.py tests\test_stopwords.py
node --check app.js
```

### 변경 푸시

```powershell
git add .
git commit -m "Your message"
git push origin main
```
