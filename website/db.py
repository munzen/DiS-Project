import psycopg2
from flask import current_app, g


def get_db():
    if 'db' not in g:
        cfg = current_app.config['DB']
        g.db = psycopg2.connect(
            dbname=cfg['dbname'],
            user=cfg['user'],
            password=cfg.get('password'),
            host=cfg['host'],
            port=cfg.get('port', 5432),
        )
    return g.db


def close_db(exception=None):
    db = g.pop('db', None)
    if db is not None:
        db.close()
