from base import BaseHandler

class PaymentHandler(BaseHandler):
    def charge(self): return 'charge'
    def refund(self): return 'refund'
    def verify(self): return 'verify'

