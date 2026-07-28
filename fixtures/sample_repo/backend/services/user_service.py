from backend import db
from backend.models import User


def create_user(user_id, name, email):
    user = User(user_id, name, email)
    return db.save_user(user)


def get_all_users():
    return list(db._FAKE_TABLE.values())


def get_user_by_id(user_id):
    return db.get_user(user_id)
