FROM python:3.12-alpine
RUN adduser -D -u 10001 app
WORKDIR /app
COPY server.py .
USER 10001
ENV PORT=8000
EXPOSE 8000
CMD ["python", "server.py"]
