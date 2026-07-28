import { UserList, loadUsers } from "./components/UserList.js";
import { NotifyButton } from "./components/NotifyButton.js";
import { dispatch } from "./utils/dispatch.js";

const list = new UserList(document.getElementById("users"));
loadUsers((users) => list.render(users));

const notifyButton = new NotifyButton(42);
notifyButton.onClick("email", "Welcome!");

dispatch("click", "users-panel");
