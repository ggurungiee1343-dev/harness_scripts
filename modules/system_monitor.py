import subprocess
import re

class SystemMonitor:
    def __init__(self, threshold_gb=2.0):
        self.threshold_gb = threshold_gb

    def get_memory_info(self):
        """macOS PhysMem 정보를 파싱하여 반환"""
        try:
            output = subprocess.check_output(["top", "-l", "1", "-n", "0"], text=True)
            mem_line = [line for line in output.split('\n') if "PhysMem" in line][0]
            
            # 예: PhysMem: 11G used (3483M wired), 4851M unused.
            unused_match = re.search(r"(\d+)([MG])\s+unused", mem_line)
            if unused_match:
                value = int(unused_match.group(1))
                unit = unused_match.group(2)
                
                unused_gb = value if unit == "G" else value / 1024.0
                return unused_gb, mem_line
            return 0, "Memory info parse error"
        except Exception as e:
            return 0, f"Error: {e}"

    def check_health(self):
        """메모리 상태 점검 및 경고 반환"""
        unused_gb, raw_info = self.get_memory_info()
        if unused_gb < self.threshold_gb:
            return False, f"⚠️ 메모리 부족 경고! 여유 공간: {unused_gb:.2f}GB\n{raw_info}"
        return True, f"✅ 시스템 양호 (여유: {unused_gb:.2f}GB)"

    def auto_heal(self, script_path):
        """0.3GB 미만 시 자율 복구 스크립트 가동"""
        unused_gb, _ = self.get_memory_info()
        if unused_gb < 0.3:
            subprocess.Popen(["bash", script_path])
            return True
        return False
