export function fetchUsers() {
  return fetch("/api/users").then((res) => res.json());
}

export function createUser(userId, name, email) {
  return fetch("/api/users/create", {
    method: "POST",
    body: JSON.stringify({ userId, name, email }),
  });
}

export function sendNotification(kind, userId, message) {
  return fetch("/api/notify", {
    method: "POST",
    body: JSON.stringify({ kind, userId, message }),
  });
}
