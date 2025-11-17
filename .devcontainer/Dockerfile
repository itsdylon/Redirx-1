FROM python:3.14.0
WORKDIR /usr/src/redirx

RUN apt-get install -y wget unzip
ENV CHROMEDRIVER_DIR=/chromedriver

RUN wget -q -O - https://dl-ssl.google.com/linux/linux_signing_key.pub | gpg --dearmor -o /usr/share/keyrings/chrome-archive-keyring.gpg && \
    echo "deb [signed-by=/usr/share/keyrings/chrome-archive-keyring.gpg] http://dl.google.com/linux/chrome/deb/ stable main" >> /etc/apt/sources.list.d/google.list && \
    apt-get update -y && \
    apt-get install -y google-chrome-stable && \
    mkdir $CHROMEDRIVER_DIR && \
    wget -q -P $CHROMEDRIVER_DIR "https://storage.googleapis.com/chrome-for-testing-public/$(google-chrome --version | cut -d ' ' -f 3)/linux64/chromedriver-linux64.zip" && \
    unzip $CHROMEDRIVER_DIR/chromedriver* -d $CHROMEDRIVER_DIR && \
    ln -s $(find $CHROMEDRIVER_DIR -type f -name "chromedriver") $CHROMEDRIVER_DIR/chromedriver

ENV PATH=$CHROMEDRIVER_DIR:$PATH

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
