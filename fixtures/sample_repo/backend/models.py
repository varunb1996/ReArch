class User:
    def __init__(self, user_id, name, email):
        self.user_id = user_id
        self.name = name
        self.email = email

    def describe(self):
        return f"{self.name} <{self.email}>"


class Admin(User):
    def __init__(self, user_id, name, email, permissions):
        super().__init__(user_id, name, email)
        self.permissions = permissions

    def describe(self):
        base = super().describe()
        return f"{base} (admin: {self.permissions})"
