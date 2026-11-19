FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
RUN useradd -m appuser
USER appuser
EXPOSE 4000
HEALTHCHECK CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:4000/health')"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "4000"]
