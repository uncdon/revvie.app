# Revvie - Review Automation SaaS

A Flask-based backend for review automation.

## Project Structure

```
revvie/
├── app/                    # Main application package
│   ├── __init__.py        # App factory (creates Flask app)
│   ├── routes/            # API endpoint definitions
│   │   ├── __init__.py
│   │   └── health.py      # Health check endpoint
│   ├── models/            # Database models (SQLAlchemy)
│   │   ├── __init__.py
│   │   └── example.py     # Example model structure
│   └── services/          # Business logic layer
│       ├── __init__.py
│       └── example_service.py
├── config.py              # Configuration settings
├── run.py                 # Application entry point
├── requirements.txt       # Python dependencies
├── .env.example          # Environment variables template
├── .gitignore            # Git ignore rules
└── README.md             # This file
```

## Setup Instructions

### 1. Create a Virtual Environment

A virtual environment keeps your project dependencies isolated from other Python projects.

```bash
# Navigate to the project folder
cd /Users/uncledanny/Desktop/revvie

# Create a virtual environment named 'venv'
python3 -m venv venv

# Activate the virtual environment
# On macOS/Linux:
source venv/bin/activate

# On Windows:
# venv\Scripts\activate
```

When activated, you'll see `(venv)` at the start of your terminal prompt.

### 2. Install Dependencies

```bash
pip install -r requirements.txt
```

### 3. Set Up Environment Variables

```bash
# Copy the example environment file
cp .env.example .env

# Edit .env with your settings (optional for development)
```

### 4. Run the Application

```bash
python run.py
```

The server will start at `http://localhost:5001`

> **Note:** Port 5001 is used because port 5000 is often occupied by macOS AirPlay Receiver.

### 5. Test the Health Endpoint

Open your browser or use curl:

```bash
curl http://localhost:5001/api/health
```

Expected response:
```json
{"status": "ok"}
```

## Development

### Adding New Routes

1. Create a new file in `app/routes/` (e.g., `reviews.py`)
2. Define a Blueprint with your endpoints
3. Register the Blueprint in `app/__init__.py`

### Adding New Models

1. Create a new file in `app/models/` (e.g., `review.py`)
2. Define your model class extending `db.Model`
3. Import it in `app/models/__init__.py`

### Adding New Services

1. Create a new file in `app/services/`
2. Add your business logic classes/functions
3. Import and use in your routes

## Deactivating the Virtual Environment

When you're done working:

```bash
deactivate
```
