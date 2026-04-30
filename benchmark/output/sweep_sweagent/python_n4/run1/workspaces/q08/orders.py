from validators import validate_request

def place_order(request):
    validate_request(request)
    return 'place_order'
