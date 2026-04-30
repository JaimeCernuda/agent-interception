from validators import validate_request

def login(request):
    validate_request(request)
    return 'login'
