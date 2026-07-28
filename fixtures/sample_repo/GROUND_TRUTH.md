# Ground truth for fixture repo

Used to hand-validate resolver output in M1-M4. Not consumed by code.

## Python — resolved (high-confidence) edges
- `backend.db` imports `backend.models.User`
- `backend.services.user_service` imports `backend.db`, `backend.models.User`
- `backend.services.user_service.create_user` calls `backend.db.save_user`
- `backend.services.user_service.get_all_users` calls `backend.db._FAKE_TABLE.values` (attribute access, not a call resolution target — fine if resolver skips it)
- `backend.services.user_service.get_user_by_id` calls `backend.db.get_user`
- `backend.app` imports `backend.services.user_service`, `backend.dispatch.dispatch`
- `backend.app.list_users` calls `user_service.get_all_users`
- `backend.app.create_user_route` calls `user_service.create_user`
- `backend.app.notify_route` calls `user_service.get_user_by_id`, `dispatch.dispatch`
- `backend.dispatch` imports `backend.services.notification_service.send_email`, `send_sms`
- `backend.models.Admin.describe` calls `super().describe` (base class `User.describe`)

## Python — low-confidence / dynamic edge
- `backend.dispatch.dispatch` calls `HANDLERS[kind](...)` — must NOT resolve to a single target.
  Expected: fan-out edge to both `notification_service.send_email` and `notification_service.send_sms`,
  flagged `resolution: "dynamic"` / low confidence.

## JS — resolved (high-confidence) edges
- `frontend/index.js` imports `UserList`, `loadUsers` from `./components/UserList.js`
- `frontend/index.js` imports `NotifyButton` from `./components/NotifyButton.js`
- `frontend/index.js` imports `dispatch` from `./utils/dispatch.js`
- `frontend/components/UserList.js` imports `fetchUsers` from `../api.js`
- `frontend/components/UserList.js.loadUsers` calls `api.fetchUsers`
- `frontend/components/NotifyButton.js` imports `sendNotification` from `../api.js`
- `frontend/components/NotifyButton.js.NotifyButton.onClick` calls `api.sendNotification`

## JS — low-confidence / dynamic edge
- `frontend/utils/dispatch.js.dispatch` calls `handlers[name](...)` — must NOT resolve to a single target.
  Expected: fan-out edge to both `onClick` and `onHover`, flagged `resolution: "dynamic"`.

## Cross-language edges (inferred via string-literal route matching, M4)
- `frontend/api.js.fetchUsers` fetch("/api/users") <-> `backend/app.py.list_users` `@app.route("/api/users")`
- `frontend/api.js.createUser` fetch("/api/users/create") <-> `backend/app.py.create_user_route` `@app.route("/api/users/create")`
- `frontend/api.js.sendNotification` fetch("/api/notify") <-> `backend/app.py.notify_route` `@app.route("/api/notify")`

All three should be flagged `resolution: "inferred-http"`, never asserted as certain.
