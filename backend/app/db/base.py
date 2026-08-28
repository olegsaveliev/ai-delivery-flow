from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Single declarative base — its metadata holds every model's table.

    All ORM models must inherit from THIS class so one ``create_all`` builds
    the full schema.
    """
