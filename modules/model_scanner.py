import requests
import json

class ModelScanner:
    def __init__(self):
        self.engines = {
            "LM Studio": "http://localhost:1234/v1/models",
            "llama.cpp": "http://localhost:8080/v1/models"
        }

    def scan_models(self):
        results = {}
        for name, url in self.engines.items():
            try:
                response = requests.get(url, timeout=2)
                if response.status_code == 200:
                    data = response.json()
                    models = [m.get("id") or m.get("name") for m in data.get("data", [])]
                    results[name] = models if models else ["연결됨 (모델 없음)"]
                else:
                    results[name] = [f"오류 ({response.status_code})"]
            except:
                results[name] = ["오프라인"]
        return results

    def get_status_report(self):
        scan = self.scan_models()
        report = "🔍 [동적 모델 감지 결과]\n"
        for engine, models in scan.items():
            model_str = ", ".join(models) if isinstance(models, list) else models
            report += f"- **{engine}**: {model_str}\n"
        return report
