import subprocess


class CLIAnything:
    def obsidian_create_note(self, vault: str, title: str, content: str):
        script = f'''
        tell application "Obsidian"
            activate
        end tell
        '''
        subprocess.run(["osascript", "-e", script], check=False)
        return {"vault": vault, "title": title, "content": content}

    def safari_open_url(self, url: str):
        subprocess.run(["open", "-a", "Safari", url], check=False)
        return True

    def safari_get_current_url(self) -> str:
        script = 'tell application "Safari" to return URL of front document'
        result = subprocess.check_output(["osascript", "-e", script], text=True)
        return result.strip()
