"""Trading scanner dashboard web application."""

from __future__ import annotations

from flask import Flask

from .config import AppConfig


def create_app(config: AppConfig | None = None) -> Flask:
    cfg = config or AppConfig.from_env()
    app = Flask(__name__)
    app.config["TRADING_CONFIG"] = cfg
    # Send static files with a 1-day max-age (CSS/JS are cache-busted by filename hash if needed).
    app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 86400

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

    from flask import request

    @app.after_request
    def add_cache_headers(response):
        if response.status_code == 200 and request.method == "GET":
            path = request.path
            # Historical scanner day pages and stock pages never change.
            import re
            if re.match(r"^/scanners/[^/]+/20\d\d-\d\d-\d\d$", path):
                response.cache_control.public = True
                response.cache_control.max_age = 3600   # 1 h client cache for past days
            elif path.startswith("/api/"):
                response.cache_control.max_age = 30     # API search: 30 s
            elif path == "/" or path.startswith(("/scanners", "/stocks")):
                response.cache_control.no_cache = True  # live pages: always revalidate
        return response

    return app


def main() -> None:
    app = create_app()
    cfg = app.config["TRADING_CONFIG"]
    app.run(host=cfg.host, port=cfg.port, debug=cfg.debug)


if __name__ == "__main__":
    main()
