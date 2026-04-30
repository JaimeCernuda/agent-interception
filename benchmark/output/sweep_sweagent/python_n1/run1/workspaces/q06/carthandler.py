from base import BaseHandler

class CartHandler(BaseHandler):
    def add(self): return 'add'
    def remove(self): return 'remove'
    def checkout(self): return 'checkout'

