from .routes import bp as login_bp

def register_login(app):
    app.register_blueprint(login_bp)
