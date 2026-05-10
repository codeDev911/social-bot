# Use a slim version of Python as the base image
FROM python:3.11-slim as app_builder

 
# Set the working directory inside the container
WORKDIR /app

# Copy the Python requirements file and install dependencies
COPY requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt 


# Copy the app files to the container
COPY . /app/
 
CMD ["sh", "-c", "prisma db push --accept-data-loss && uvicorn main:app --host 0.0.0.0 --port 8080"]
