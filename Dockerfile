FROM mcr.microsoft.com/playwright/python:v1.60.0-noble

WORKDIR /usr/src/app

# Install Python dependencies from the project's requirement file
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy project files
COPY . .

# Ensure Playwright browsers (image usually has them, but run safely)
RUN playwright install --with-deps || true

ENV PYTHONUNBUFFERED=1

CMD ["python", "-m", "src.scraper"]
