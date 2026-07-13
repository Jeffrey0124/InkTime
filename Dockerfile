FROM quay.io/fedora/python-312:latest

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    TZ=Asia/Shanghai

USER root
WORKDIR /app

COPY requirements.txt ./
RUN python -m pip install --upgrade pip \
    && python -m pip install -r requirements.txt

COPY . .
COPY docker/config.py ./config.py
COPY docker/fonts/NotoSansSC-VF.ttf /usr/local/share/fonts/NotoSansSC-VF.ttf

EXPOSE 8766

CMD ["python", "server.py"]
