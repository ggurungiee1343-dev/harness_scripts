import json
import os
import subprocess
import time

try:
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
except ImportError:
    Credentials = None
    build = None


class GDriveFailover:
    def __init__(self, credentials_path: str = "~/.hermes/gdrive_credentials.json", token_path: str = "~/.hermes/gdrive_token.json", watch_folder: str = "Hermes Failover"):
        self.credentials_path = os.path.expanduser(credentials_path)
        self.token_path = os.path.expanduser(token_path)
        self.watch_folder = watch_folder
        self.service = self._auth() if build else None

    def _auth(self):
        if not os.path.exists(self.token_path):
            raise FileNotFoundError(f"Missing token file: {self.token_path}")
        creds = Credentials.from_authorized_user_file(self.token_path)
        return build("drive", "v3", credentials=creds)

    def _list_command_files(self):
        if not self.service:
            return []
        q = "name = 'command.json' and trashed = false"
        result = self.service.files().list(q=q, fields="files(id, name, modifiedTime)").execute()
        return result.get("files", [])

    def _download_text(self, file_id: str) -> str:
        req = self.service.files().get_media(fileId=file_id)
        return req.execute().decode("utf-8")

    def _execute_command(self, cmd: dict):
        allowed = {"/status", "/dreaming", "/restart_telegram"}
        action = cmd.get("action")
        if action not in allowed:
            return {"ok": False, "error": "command not allowed"}
        try:
            out = subprocess.check_output(["python", "hermes_local.py", action], text=True)
            return {"ok": True, "output": out}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def watch_loop(self, interval: int = 30):
        while True:
            for file in self._list_command_files():
                content = self._download_text(file["id"])
                cmd = json.loads(content)
                self._execute_command(cmd)
            time.sleep(interval)
