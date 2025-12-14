from flask import Flask

from .routes import main_bp


def create_app() -> Flask:
    app = Flask(__name__)
    app.config['SECRET_KEY'] = 'change-me'
    app.register_blueprint(main_bp)
    return app
