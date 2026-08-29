"""Trading scanner dashboard web application."""

from __future__ import annotations

from flask import Flask

from .config import AppConfig


def create_app(config: AppConfig | None = None) -> Flask:
    cfg = config or AppConfig.from_env()
    app = Flask(__name__)
    app.config["TRADING_CONFIG"] = cfg

    from .helpers import close_db
    from .routes.api import api_bp
    from .routes.main import main_bp
    from .routes.scanners import scanners_bp
    from .routes.stocks import stocks_bp

    app.register_blueprint(main_bp)
    app.register_blueprint(scanners_bp)
    app.register_blueprint(stocks_bp)
    app.register_blueprint(api_bp)
    app.teardown_appcontext(close_db)
    return app


def main() -> None:
    app = create_app()
    cfg = app.config["TRADING_CONFIG"]
    app.run(host=cfg.host, port=cfg.port, debug=cfg.debug)


if __name__ == "__main__":
    main()
