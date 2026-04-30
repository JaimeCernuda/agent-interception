from validators import validate_request

def delete_user(request):
    validate_request(request)
    return 'delete_user'
