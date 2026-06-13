from redis import Redis
import os
from dotenv import load_dotenv
load_dotenv()

r = Redis.from_url(os.getenv("REDIS_URL"), ssl_cert_reqs=None)

print(r.ping())