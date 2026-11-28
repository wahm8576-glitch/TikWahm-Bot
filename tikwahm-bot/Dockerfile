FROM python:3.12-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=10000

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends fonts-noto-core fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY bot.py ./bot.py
COPY proxy_admin.py ./proxy_admin.py
COPY tikwahm_loading.gif ./tikwahm_loading.gif
COPY video_widget.html ./video_widget.html

RUN useradd --system --create-home --home-dir /home/tikwahm --shell /usr/sbin/nologin tikwahm \
    && chown -R tikwahm:tikwahm /app /home/tikwahm
USER tikwahm

EXPOSE 10000
CMD ["sh", "-c", "exec uvicorn bot:app --host 0.0.0.0 --port ${PORT:-10000} --workers 1"]
