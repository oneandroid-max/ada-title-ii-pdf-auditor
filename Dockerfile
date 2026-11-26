FROM apify/actor-python:3.12

WORKDIR /usr/src/app

COPY requirements.txt ./
RUN python3 -m pip install --no-cache-dir --upgrade pip \
    && python3 -m pip install --no-cache-dir -r requirements.txt

COPY src ./src

RUN useradd --create-home apify \
    && chown -R apify:apify /usr/src/app
USER apify

CMD ["python3", "-m", "src.main"]
