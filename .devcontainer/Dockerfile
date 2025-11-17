FROM python:3.14.0
WORKDIR /usr/src/redirx

RUN apt-get update

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
