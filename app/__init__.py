from flask import Flask


def create_app() -> Flask:
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024

    from .views.price import price_bp
    from .views.automation import automation_bp
    from .views.main import main_bp

    app.register_blueprint(main_bp)
    app.register_blueprint(price_bp, url_prefix="/price")
    app.register_blueprint(automation_bp, url_prefix="/automation")

    return app
