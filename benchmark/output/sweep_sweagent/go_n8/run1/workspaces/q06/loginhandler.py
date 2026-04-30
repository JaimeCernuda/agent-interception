from base import BaseHandler

class LoginHandler(BaseHandler):
    def login(self): return 'login'
    def logout(self): return 'logout'
    def validate(self): return 'validate'

