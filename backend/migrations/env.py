import os
from alembic import context
from sqlalchemy import create_engine
from sqlalchemy.pool import NullPool
from app.models import Base
from app import assessment_models
from app import template_models
from app import grading_models
from app import mapping_models

engine = create_engine(os.environ["DATABASE_URL"], poolclass=NullPool)
with engine.connect() as connection:
    context.configure(connection=connection, target_metadata=Base.metadata)
    with context.begin_transaction():
        context.run_migrations()
