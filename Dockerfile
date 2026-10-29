FROM python:3.12-slim
WORKDIR /app
COPY traffic ./traffic
RUN mkdir -p /app/data
EXPOSE 8000
CMD ["python", "-m", "traffic", "--host", "0.0.0.0", "--simulate"]
