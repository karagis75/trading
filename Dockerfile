FROM python:3.12-slim-bookworm

# Container bind is 0.0.0.0 so published compose ports reach Flask.
# Host runs of `python -m webapp` still default to 127.0.0.1 via webapp/config.py.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=Asia/Kolkata \
    TRADING_WEB_HOST=0.0.0.0 \
    TRADING_WEB_PORT=8000

RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata ca-certificates \
    && ln -snf /usr/share/zoneinfo/Asia/Kolkata /etc/localtime \
    && echo Asia/Kolkata > /etc/timezone \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

CMD ["python", "-m", "webapp"]
