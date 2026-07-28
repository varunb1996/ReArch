import { sendNotification } from "../api.js";

export class NotifyButton {
  constructor(userId) {
    this.userId = userId;
  }

  onClick(kind, message) {
    return sendNotification(kind, this.userId, message);
  }
}
