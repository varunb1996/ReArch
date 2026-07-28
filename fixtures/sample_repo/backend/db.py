from backend.models import User

_FAKE_TABLE = {}


def get_user(user_id):
    return _FAKE_TABLE.get(user_id)


def save_user(user: User):
    _FAKE_TABLE[user.user_id] = user
    return user
