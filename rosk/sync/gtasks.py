"""Google Tasks API v1 client (public API, token from GNOME Online Accounts)."""

from .http import request

BASE = "https://tasks.googleapis.com/tasks/v1"


class TasksClient:
    def __init__(self, token_provider):
        # token_provider(force_refresh: bool) -> str
        self._token_provider = token_provider
        self._token = None

    def _call(self, method, path, params=None, body=None):
        from .http import AuthError

        if self._token is None:
            self._token = self._token_provider(False)
        try:
            return request(method, BASE + path, self._token, params, body)
        except AuthError:
            self._token = self._token_provider(True)
            return request(method, BASE + path, self._token, params, body)

    def _paged(self, path, params):
        params = dict(params)
        while True:
            page = self._call("GET", path, params) or {}
            yield from page.get("items", [])
            token = page.get("nextPageToken")
            if not token:
                break
            params["pageToken"] = token

    # Task lists
    def list_tasklists(self):
        return list(self._paged("/users/@me/lists", {"maxResults": 100}))

    def insert_tasklist(self, title):
        return self._call("POST", "/users/@me/lists", body={"title": title})

    def patch_tasklist(self, list_id, title):
        return self._call("PATCH", f"/users/@me/lists/{list_id}", body={"title": title})

    def delete_tasklist(self, list_id):
        self._call("DELETE", f"/users/@me/lists/{list_id}")

    # Tasks
    def list_tasks(self, list_id, updated_min=None):
        params = {
            "maxResults": 100,
            "showCompleted": "true",
            "showHidden": "true",
            "showDeleted": "true" if updated_min else "false",
            "updatedMin": updated_min,
        }
        return list(self._paged(f"/lists/{list_id}/tasks", params))

    def get_task(self, list_id, task_id):
        return self._call("GET", f"/lists/{list_id}/tasks/{task_id}")

    def insert_task(self, list_id, body, parent=None, previous=None):
        params = {"parent": parent, "previous": previous}
        return self._call("POST", f"/lists/{list_id}/tasks", params, body)

    def patch_task(self, list_id, task_id, body):
        return self._call("PATCH", f"/lists/{list_id}/tasks/{task_id}", body=body)

    def delete_task(self, list_id, task_id):
        self._call("DELETE", f"/lists/{list_id}/tasks/{task_id}")

    def move_task(self, list_id, task_id, parent=None, previous=None, destination=None):
        params = {"parent": parent, "previous": previous, "destinationTasklist": destination}
        return self._call("POST", f"/lists/{list_id}/tasks/{task_id}/move", params)
