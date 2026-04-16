FROM python:3.9-slim
WORKDIR /app
RUN pip install flask
COPY app.py .
RUN mkdir data
EXPOSE 5000
CMD ["python", "app.py"]