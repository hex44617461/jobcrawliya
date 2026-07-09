# Playwright 실행에 필요한 브라우저/시스템 라이브러리가 포함된 Python 이미지를 사용합니다.
FROM mcr.microsoft.com/playwright/python:v1.60.0-noble

# 컨테이너 안에서 앱이 실행될 기본 작업 디렉터리입니다.
WORKDIR /usr/src/app

# 프로젝트의 Python 의존성 목록을 먼저 복사해 Docker 빌드 캐시를 활용합니다.
COPY requirements.txt ./
# requirements.txt에 적힌 직접 의존성을 설치합니다.
RUN pip install --no-cache-dir -r requirements.txt

# 실제 소스 코드와 설정 파일을 컨테이너로 복사합니다.
COPY . .

# 기본 이미지에 브라우저가 들어 있어도, 누락된 경우를 대비해 Playwright 의존성을 확인합니다.
RUN playwright install --with-deps || true

# Python 출력 버퍼링을 꺼서 Docker 로그가 바로 보이게 합니다.
ENV PYTHONUNBUFFERED=1

# 별도 크롤러 컨테이너로 실행할 때 src.scraper를 바로 실행합니다.
CMD ["python", "-m", "src.scraper"]
