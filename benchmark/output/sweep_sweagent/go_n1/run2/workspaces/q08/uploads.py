from validators import validate_request

def upload(request):
    validate_request(request)
    return 'upload'
