"""
Frontend routes - serves HTML pages.
"""

import os
from flask import Blueprint, send_from_directory

frontend_bp = Blueprint('frontend', __name__)

# Path to frontend folder
FRONTEND_FOLDER = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'frontend')


@frontend_bp.route('/')
def index():
    """Serve signup page as default."""
    return send_from_directory(FRONTEND_FOLDER, 'signup.html')


@frontend_bp.route('/login')
@frontend_bp.route('/login.html')
def login():
    """Serve login page."""
    return send_from_directory(FRONTEND_FOLDER, 'login.html')


@frontend_bp.route('/signup')
@frontend_bp.route('/signup.html')
def signup():
    """Serve signup page."""
    return send_from_directory(FRONTEND_FOLDER, 'signup.html')


@frontend_bp.route('/onboarding')
@frontend_bp.route('/onboarding.html')
def onboarding():
    """Serve onboarding page."""
    return send_from_directory(FRONTEND_FOLDER, 'onboarding.html')


@frontend_bp.route('/dashboard')
@frontend_bp.route('/dashboard.html')
def dashboard():
    """Serve dashboard page."""
    return send_from_directory(FRONTEND_FOLDER, 'dashboard.html')
