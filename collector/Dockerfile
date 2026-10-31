FROM python:3.12-alpine
RUN apk add --no-cache iputils tzdata && addgroup -S monitor && adduser -S -G monitor monitor
COPY --chown=monitor:monitor app.py /app/app.py
RUN chmod 755 /app && chmod 644 /app/app.py
WORKDIR /app
USER monitor
EXPOSE 8788 5514/udp
CMD ["python", "-u", "/app/app.py"]
