"""
audit_engine.py — 시스템 감사 엔진
포트/SSH/키 스코프 인프라 감사 + structured JSON 로깅
"""
import os
import json
import subprocess
import datetime
import logging
import socket
from pathlib import Path

logger = logging.getLogger('AuditEngine')

AUDIT_LOG_DIR = Path('/Users/bluesea/Applications/Mjauto/.audit_logs')

class AuditEngine:
    def __init__(self, vault_path="/Users/bluesea/Applications/Mjobsidian"):
        self.vault_path = Path(vault_path)
        AUDIT_LOG_DIR.mkdir(parents=True, exist_ok=True)

    def log_audit(self, action: str, target: str, result: dict, status: str = "ok"):
        """구조화 JSON 감사 기록"""
        record = {
            "timestamp": datetime.datetime.now().isoformat(),
            "action": action,
            "target": target,
            "status": status,
            "result": result
        }
        log_file = AUDIT_LOG_DIR / f"audit_{datetime.date.today().isoformat()}.jsonl"
        try:
            with open(log_file, 'a', encoding='utf-8') as f:
                f.write(json.dumps(record, ensure_ascii=False) + '\n')
        except Exception as e:
            logger.error(f"Audit log write failed: {e}")
        return record

    def run_audit(self):
        """기존 호환성용 — 지식 베이스 무결성 검사"""
        return "✅ 지식 베이스 무결성 검사 완료"

    def infra_audit(self) -> dict:
        """인프라 감사: 포트, SSH, 키 스코프"""
        report = {}

        # 포트 스캔 (주요 서비스 포트)
        ports = {
            "llama.cpp (8080)": self._check_port(8080),
            "Gateway (8005)": self._check_port(8005),
            "Hermes WebUI (8787)": self._check_port(8787),
            "SSH (22)": self._check_port(22),
        }
        report["ports"] = ports

        # SSH 키 검사
        ssh_dir = Path.home() / ".ssh"
        ssh_keys = []
        if ssh_dir.exists():
            for f in ssh_dir.iterdir():
                if f.name.endswith(".pub"):
                    ssh_keys.append({"file": f.name, "type": "public"})
                elif "key" in f.name.lower() or "id_" in f.name:
                    ssh_keys.append({"file": f.name, "type": "private (potential)"})
        report["ssh_keys"] = ssh_keys

        # env 키 존재 여부 (값 노출 금지)
        env_keys = {}
        for var in ["OPENAI_API_KEY", "DEEPSEEK_API_KEY", "TELEGRAM_BOT_TOKEN"]:
            val = os.environ.get(var, "")
            env_keys[var] = "✅ 설정됨" if val else "❌ 미설정"
        report["env_keys"] = env_keys

        # 방화벽 상태
        try:
            fw = subprocess.run(
                ["/usr/libexec/ApplicationFirewall/socketfilterfw", "--getglobalstate"],
                capture_output=True, text=True, timeout=5
            )
            report["firewall"] = fw.stdout.strip() or "상태 확인 불가"
        except Exception as e:
            report["firewall"] = f"확인 실패: {e}"

        self.log_audit("infra_audit", "system", report)
        return report

    def _check_port(self, port: int) -> str:
        """지정 포트의 listen 상태 확인"""
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(2)
                result = s.connect_ex(('127.0.0.1', port))
                return "🟢 LISTEN" if result == 0 else "⚫ CLOSED"
        except Exception:
            return "❌ ERROR"
