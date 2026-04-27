# Revvie — Review Automation SaaS

Revvie is a backend-driven SaaS platform that automates customer review requests for businesses.  
It allows businesses to send automated follow-ups to customers and increase online reviews without manual effort.

This project demonstrates backend system design, API development, database management, and third-party integrations.

---

## Features

- Send automated review request emails/SMS
- Store and manage customer data
- REST API endpoints for managing users and reviews
- Environment-based configuration system
- Modular service-based architecture
- Health monitoring endpoint
- Database models using SQLAlchemy

---

## Tech Stack

Backend:
- Python
- Flask
- SQLAlchemy

Integrations:
- SendGrid API (email automation)

Tools:
- Git
- Virtual environments
- Environment variables (.env)

Database:
- SQLite / PostgreSQL (whichever you used)

---

## Project Structure

revvie/
├── app/
│   ├── routes/        # API endpoints
│   ├── models/        # Database models
│   ├── services/      # Business logic
├── config.py
├── run.py
├── requirements.txt
├── .env.example
├── .gitignore
└── README.md

---

## Setup Instructions

### 1. Create Virtual Environment

python3 -m venv venv  
source venv/bin/activate

### 2. Install Dependencies

pip install -r requirements.txt

### 3. Configure Environment Variables

cp .env.example .env

Update all variables and API keys.

### 4. Run the Server

python run.py

Server runs at:

http://localhost:5001

---

## API Health Check

curl http://localhost:5001/api/health

Expected response:

{"status": "ok"}

---

## Author

Daniel Israel  
GitHub: https://github.com/uncdon
