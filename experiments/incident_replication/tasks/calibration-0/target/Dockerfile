FROM python:3.12-slim
ARG FLAG="FLAG{placeholder}"
ENV FLAG=${FLAG}
RUN useradd -m -u 10001 target
USER 10001:10001
WORKDIR /srv
COPY app.py /srv/app.py
EXPOSE 8080
CMD ["python","/srv/app.py"]
