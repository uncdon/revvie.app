"""
Example model to demonstrate SQLAlchemy model structure.
Replace or extend this with your actual models (User, Review, etc.)
"""

from app import db
from datetime import datetime


class Example(db.Model):
    """
    Example database model.

    This shows the basic structure of a SQLAlchemy model:
    - Define columns with their types
    - Set constraints like primary_key, nullable, unique
    - Add relationships to other models
    """
    __tablename__ = 'examples'  # Name of the database table

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    description = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        """Convert model to dictionary for JSON serialization."""
        return {
            'id': self.id,
            'name': self.name,
            'description': self.description,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None
        }

    def __repr__(self):
        """String representation for debugging."""
        return f'<Example {self.name}>'
