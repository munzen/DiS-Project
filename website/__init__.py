from flask import Flask
from .db import close_db


def create_app():
    app = Flask(__name__)
    app.config['SECRET_KEY'] = 'oN9aM43ixG4db62Lgr2' # Should
    app.config['DB'] = {
        'host': 'localhost',
        'dbname': 'portfolio_app',
        'user': 'postgres',
        'password': ':85qJe@xy3G4',
    }

    app.teardown_appcontext(close_db)

    from .auth import bp as auth_bp
    from .views import bp as views_bp
    app.register_blueprint(auth_bp)
    app.register_blueprint(views_bp)

    return app
