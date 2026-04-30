from validators import validate_request

def charge(request):
    validate_request(request)
    return 'charge'
