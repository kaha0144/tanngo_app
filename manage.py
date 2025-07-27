# manage.py
from app import app, db
from flask_migrate import Migrate
from flask.cli import with_appcontext
import click

migrate = Migrate(app, db)

@app.cli.command("db-init")
@with_appcontext
def db_init():
    """初回の DB 初期化"""
    from flask_migrate import init
    init()

@app.cli.command("db-migrate")
@with_appcontext
def db_migrate():
    """マイグレーションファイル作成"""
    from flask_migrate import migrate
    migrate(message="initial migration")

@app.cli.command("db-upgrade")
@with_appcontext
def db_upgrade():
    """マイグレーションの適用"""
    from flask_migrate import upgrade
    upgrade()
