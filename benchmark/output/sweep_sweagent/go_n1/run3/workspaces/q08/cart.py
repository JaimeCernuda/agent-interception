from validators import validate_request

def checkout(request):
    validate_request(request)
    return 'checkout'
