"""
Configuration settings for the Flask application.
Different configurations for development, testing, and production.
"""

import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    """Base configuration with settings common to all environments."""
    SECRET_KEY = os.environ.get('SECRET_KEY') or 'dev-secret-key-change-in-production'
    SQLALCHEMY_TRACK_MODIFICATIONS = False


class DevelopmentConfig(Config):
    """Development configuration with debug mode enabled."""
    DEBUG = True
    SQLALCHEMY_DATABASE_URI = os.environ.get('DATABASE_URL') or 'sqlite:///revvie_dev.db'


class ProductionConfig(Config):
    """Production configuration with debug mode disabled."""
    DEBUG = False
    SQLALCHEMY_DATABASE_URI = os.environ.get('DATABASE_URL')


class TestingConfig(Config):
    """Testing configuration with a separate test database."""
    TESTING = True
    SQLALCHEMY_DATABASE_URI = 'sqlite:///revvie_test.db'


config = {
    'development': DevelopmentConfig,
    'production': ProductionConfig,
    'testing': TestingConfig,
    'default': DevelopmentConfig
}
