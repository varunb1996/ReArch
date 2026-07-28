def send_email(user, message):
    return f"EMAIL to {user.email}: {message}"


def send_sms(user, message):
    return f"SMS to {user.name}: {message}"
