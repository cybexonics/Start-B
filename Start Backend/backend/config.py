import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY') or 'your-secret-key-here'
    MONGO_URI = os.environ.get('MONGO_URI') or 'mongodb+srv://star_tailor:fljC9lR6aUPZffka@cluster0.sfkrwck.mongodb.net'
    JWT_ACCESS_TOKEN_EXPIRES = 86400  # 24 hours in seconds
# z9fWtfJ4auOj4KpT  mongodb+srv://startailor657_db_user:z9fWtfJ4auOj4KpT@cluster0.sfkrwck.mongodb.net/
class DevelopmentConfig(Config):
    DEBUG = True

class ProductionConfig(Config):
    DEBUG = False

config = {
    'development': DevelopmentConfig,
    'production': ProductionConfig,
    'default': DevelopmentConfig
}