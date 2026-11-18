import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text


class MigrationTests(unittest.TestCase):
    def test_fresh_database_upgrade_is_repeatable(self):
        with TemporaryDirectory() as directory:
            url = "sqlite:///" + str(Path(directory) / "test.db")
            with patch.dict(os.environ, {"DATABASE_URL": url}):
                command.upgrade(Config("alembic.ini"), "head")
                command.upgrade(Config("alembic.ini"), "head")
            engine = create_engine(url)
            self.assertTrue({"jobs", "segments", "alembic_version"} <= set(inspect(engine).get_table_names()))
            self.assertIn("run_token", {column["name"] for column in inspect(engine).get_columns("jobs")})
            engine.dispose()
