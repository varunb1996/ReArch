from backend.services.notification_service import send_email, send_sms

HANDLERS = {
    "email": send_email,
    "sms": send_sms,
}


def dispatch(kind, user, message):
    # Dynamic/ambiguous call site: static analysis cannot know which handler
    # runs without evaluating `kind` at runtime. Resolvers should emit this
    # edge as low-confidence, fanning out to every value in HANDLERS rather
    # than guessing a single target.
    handler = HANDLERS[kind]
    return handler(user, message)
