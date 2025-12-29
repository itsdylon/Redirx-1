FROM python:3.11
WORKDIR /usr/src/redirx

RUN apt-get update

COPY pyproject.toml ./
RUN pip install --no-cache-dir -e .

COPY . .
