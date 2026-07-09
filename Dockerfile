FROM mcr.microsoft.com/playwright/python:1.60.0

WORKDIR /usr/src/app

# Install Python dependencies from the project's requirement file
COPY requirement.txt ./
RUN pip install --no-cache-dir -r requirement.txt

# Copy project files
COPY . .

# Ensure Playwright browsers (image usually has them, but run safely)
RUN playwright install --with-deps || true

ENV PYTHONUNBUFFERED=1

CMD ["python", "app.py"]
