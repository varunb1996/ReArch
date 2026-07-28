import { fetchUsers } from "../api.js";

export function loadUsers(onLoaded) {
  return fetchUsers().then((users) => onLoaded(users));
}

export class UserList {
  constructor(container) {
    this.container = container;
  }

  render(users) {
    this.container.textContent = users.map((u) => u.name).join(", ");
  }
}
