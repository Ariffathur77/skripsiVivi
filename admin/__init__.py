from .routes import bp as admin_bp

def register_admin(app):
    app.register_blueprint(admin_bp)