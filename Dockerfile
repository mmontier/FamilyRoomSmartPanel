FROM python:3.12-slim

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

EXPOSE 8777
CMD ["sh", "-c", "python -m uvicorn app.main:app --host ${PANEL_HOST:-0.0.0.0} --port ${PANEL_PORT:-8777} --no-access-log"]
