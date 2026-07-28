from backend.services import user_service
from backend.dispatch import dispatch


class App:
    def route(self, path):
        def decorator(fn):
            return fn
        return decorator


app = App()


@app.route("/api/users")
def list_users():
    return user_service.get_all_users()


@app.route("/api/users/create")
def create_user_route(user_id, name, email):
    return user_service.create_user(user_id, name, email)


@app.route("/api/notify")
def notify_route(kind, user_id, message):
    user = user_service.get_user_by_id(user_id)
    return dispatch(kind, user, message)
