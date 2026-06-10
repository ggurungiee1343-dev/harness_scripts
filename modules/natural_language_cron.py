import os
import re
import subprocess
import uuid
from datetime import datetime


class NaturalLanguageCron:
    def __init__(self):
        self.launch_agents = os.path.expanduser("~/Library/LaunchAgents")
        os.makedirs(self.launch_agents, exist_ok=True)

    def parse(self, text: str) -> str:
        t = text.strip()
        m = re.search(r"매일\s+(오전|오후)\s*(\d+)시", t)
        if m:
            ampm, hour = m.groups()
            hour = int(hour) % 12
            if ampm == "오후":
                hour += 12
            return f"0 {hour} * * *"
        m = re.search(r"매주\s*(월|화|수|목|금|토|일)\s+(오전|오후)\s*(\d+)시", t)
        if m:
            dow_map = {"일": 0, "월": 1, "화": 2, "수": 3, "목": 4, "금": 5, "토": 6}
            day, ampm, hour = m.groups()
            hour = int(hour) % 12
            if ampm == "오후":
                hour += 12
            return f"0 {hour} * * {dow_map[day]}"
        raise ValueError("지원하지 않는 자연어 일정 형식입니다")

    def schedule(self, cron_expr: str, command: str) -> str:
        minute, hour, _, _, weekday = cron_expr.split()
        label = f"com.hermes.cron.{uuid.uuid4().hex[:8]}"
        plist_path = os.path.join(self.launch_agents, f"{label}.plist")
        plist = f'''<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>Label</key><string>{label}</string>
<key>ProgramArguments</key><array><string>/bin/bash</string><string>-lc</string><string>{command}</string></array>
<key>StartCalendarInterval</key><dict>
<key>Minute</key><integer>{minute}</integer>
<key>Hour</key><integer>{hour}</integer>
<key>Weekday</key><integer>{weekday}</integer>
</dict>
<key>StandardOutPath</key><string>/tmp/{label}.out</string>
<key>StandardErrorPath</key><string>/tmp/{label}.err</string>
</dict></plist>'''
        with open(plist_path, "w", encoding="utf-8") as f:
            f.write(plist)
        return plist_path

    def load(self, plist_path: str):
        subprocess.run(["launchctl", "load", plist_path], check=False)
        return {"loaded": True, "plist": plist_path, "ts": datetime.now().isoformat()}
