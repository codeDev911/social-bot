from prisma import Client 


prisma = Client()

def get_db():
    db = prisma
    try:
        yield db
    finally:
        db.disconnect()  
