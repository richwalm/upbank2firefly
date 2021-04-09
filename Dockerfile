FROM tiangolo/uwsgi-nginx-flask:python3.8-alpine
RUN apk --update add ca-certificates
COPY ./app /app
