import os
import json
import time
import requests
from datetime import datetime
from dateutil import parser
import threading

# Use relative import or sys.path if needed
from .graph_engine import GraphMemoryEngine

class MemoryConsolidator:
    def __init__(
        self, 
        episodic_path="/Users/bluesea/Applications/Mjauto/Scripts/harness_memory.json",
        llm_endpoint="http://127.0.0.1:8080/v1/chat/completions",
        idle_threshold_seconds=300
    ):
        self.episodic_path = episodic_path
        self.llm_endpoint = llm_endpoint
        self.idle_threshold_seconds = idle_threshold_seconds
        self.graph_engine = GraphMemoryEngine()
        
        # Keep track of how many messages we've processed
        self.state_file = "/Users/bluesea/Applications/Mjauto/Scripts/hermes/memory_engine/consolidator_state.json"
        self.last_processed_index = self._load_state()
        self.running = False
        self._thread = None

    def _load_state(self):
        if os.path.exists(self.state_file):
            try:
                with open(self.state_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    return data.get("last_processed_index", -1)
            except:
                pass
        return -1

    def _save_state(self):
        try:
            with open(self.state_file, 'w', encoding='utf-8') as f:
                json.dump({"last_processed_index": self.last_processed_index}, f)
        except Exception as e:
            print(f"Failed to save state: {e}")

    def get_unprocessed_logs(self):
        if not os.path.exists(self.episodic_path):
            return [], -1
            
        try:
            with open(self.episodic_path, 'r', encoding='utf-8') as f:
                logs = json.load(f)
        except Exception as e:
            print(f"Error reading episodic memory: {e}")
            return [], -1

        if not logs:
            return [], -1

        # Check if idle time has passed
        last_log = logs[-1]
        timestamp_str = last_log.get("timestamp")
        
        if timestamp_str:
            try:
                last_time = parser.parse(timestamp_str)
                # Compare naive with naive or aware with aware. 
                now = datetime.now(last_time.tzinfo)
                diff = (now - last_time).total_seconds()
                if diff < self.idle_threshold_seconds:
                    # Still active, do not consolidate yet
                    return [], -1
            except Exception as e:
                print(f"Timestamp parsing error: {e}")
                
        # Return unprocessed items
        new_index = len(logs) - 1
        if new_index > self.last_processed_index:
            unprocessed = logs[self.last_processed_index + 1 : new_index + 1]
            return unprocessed, new_index
        return [], -1

    def _extract_triples_via_llm(self, text_chunk):
        """Call the local Llama server to extract Subject-Predicate-Object triples."""
        system_prompt = (
            "You are an AI tasked with extracting facts from text. "
            "Extract knowledge graph triples in the exact format: [Subject] | [Predicate] | [Object]\n"
            "Only output the triples, one per line. Do not output anything else."
        )
        
        headers = {"Content-Type": "application/json"}
        payload = {
            "model": "gemma-4-26B",
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"Extract triples from this:\n\n{text_chunk}"}
            ],
            "temperature": 0.1,
            "max_tokens": 512
        }

        try:
            response = requests.post(self.llm_endpoint, json=payload, headers=headers, timeout=60)
            if response.status_code == 200:
                result = response.json()["choices"][0]["message"]["content"]
                return result
        except Exception as e:
            print(f"LLM extraction error: {e}")
        return ""

    def process_logs(self):
        unprocessed, new_index = self.get_unprocessed_logs()
        if not unprocessed:
            return

        print(f"Consolidating {len(unprocessed)} new memory items...")
        
        # Group text to avoid too many API calls
        combined_text = "\\n".join([f"{item.get('role', 'unknown')}: {item.get('content', '')}" for item in unprocessed])
        
        # Simple chunking if text is too long (omitted for brevity, assume < context limit)
        triples_text = self._extract_triples_via_llm(combined_text)
        
        for line in triples_text.strip().split('\\n'):
            parts = [p.strip() for p in line.split('|')]
            if len(parts) == 3:
                subject, predicate, obj = parts
                self.graph_engine.add_relation(subject, predicate, obj)
                
        self.last_processed_index = new_index
        self._save_state()
        print("Consolidation complete.")

    def run_loop(self):
        self.running = True
        while self.running:
            self.process_logs()
            time.sleep(60) # check every minute

    def start_background(self):
        if self._thread is None or not self._thread.is_alive():
            self._thread = threading.Thread(target=self.run_loop, daemon=True)
            self._thread.start()
            print("Memory Consolidator started in background.")

    def stop(self):
        self.running = False
        if self._thread:
            self._thread.join()

if __name__ == "__main__":
    consolidator = MemoryConsolidator(idle_threshold_seconds=10) # 10s for quick test
    consolidator.process_logs()
