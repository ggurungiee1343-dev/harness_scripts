import os
import re
import math
import collections
import unicodedata
import datetime

BASE_DIR = "/Users/bluesea/Applications/Mjobsidian"
REPORT_PATH = os.path.join(BASE_DIR, "wiki/00_Meta/보관소_유사도_분석보고서.md")

EXCLUDE_DIRS = {
    ".obsidian", ".smart-env", ".tmp.drivedownload", ".tmp.driveupload", 
    ".vscode", "graphify-out", "outputs", "raw", ".git"
}

def get_tokens(text):
    normalized = unicodedata.normalize('NFC', text)
    words = re.findall(r'[a-zA-Z0-9가-힣]+', normalized)
    return [w.lower() for w in words if len(w) > 1]

def scan_vault():
    doc_data = []
    for root, dirs, files in os.walk(BASE_DIR):
        dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]
        for file in files:
            if file.endswith(".md") and not file.startswith("."):
                full_path = os.path.join(root, file)
                rel_path = os.path.relpath(full_path, BASE_DIR)
                
                try:
                    with open(full_path, "r", encoding="utf-8", errors="ignore") as f:
                        content = f.read()
                except Exception as e:
                    continue
                
                if len(content.strip()) < 100:
                    continue
                    
                tokens = get_tokens(content)
                if len(tokens) < 15:
                    continue
                    
                doc_data.append({
                    "path": rel_path,
                    "tokens": tokens,
                    "title": file.replace(".md", "")
                })
    return doc_data

def calculate_similarities(doc_data):
    if len(doc_data) < 2:
        return []
    N = len(doc_data)
    df = collections.defaultdict(int)
    doc_tfs = []
    
    for doc in doc_data:
        tf = collections.defaultdict(int)
        for token in doc["tokens"]:
            tf[token] += 1
        doc_tfs.append(tf)
        for token in set(doc["tokens"]):
            df[token] += 1
            
    idf = {}
    for token, count in df.items():
        idf[token] = math.log(N / count) if count > 0 else 0.0
        
    vectors = []
    norms = []
    for i in range(N):
        tf = doc_tfs[i]
        vec = {}
        val_sum_sq = 0.0
        for token, count in tf.items():
            val = count * idf[token]
            vec[token] = val
            val_sum_sq += val * val
        vectors.append(vec)
        norms.append(math.sqrt(val_sum_sq))
        
    similar_pairs = []
    for i in range(N):
        for j in range(i + 1, N):
            norm_i = norms[i]
            norm_j = norms[j]
            if norm_i == 0 or norm_j == 0:
                continue
                
            dot_product = 0.0
            vec_i = vectors[i]
            vec_j = vectors[j]
            
            if len(vec_i) < len(vec_j):
                for token, val in vec_i.items():
                    if token in vec_j:
                        dot_product += val * vec_j[token]
            else:
                for token, val in vec_j.items():
                    if token in vec_i:
                        dot_product += val * vec_i[token]
                        
            similarity = dot_product / (norm_i * norm_j)
            if similarity >= 0.40:
                similar_pairs.append({
                    "doc1": doc_data[i]["path"],
                    "doc2": doc_data[j]["path"],
                    "similarity": similarity
                })
                
    similar_pairs.sort(key=lambda x: x["similarity"], reverse=True)
    return similar_pairs

def write_report(pairs):
    os.makedirs(os.path.dirname(REPORT_PATH), exist_ok=True)
    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    
    lines = [
        "# 🔍 보관소 내 중복/유사 문서 분석 보고서",
        "",
        f"- **분석 일시:** {now_str}",
        f"- **대상 디렉토리:** `{BASE_DIR}`",
        "- **분석 방법:** TF-IDF 기반 코사인 유사도(Cosine Similarity) 연산",
        "",
        "본 보고서는 내용이 40% 이상 유사한 문서들을 분석한 결과입니다. 95% 이상 중복되는 파일은 자율 병합의 대상이 됩니다.",
        "",
        "## 📌 중복 및 유사도 스캔 결과 요약",
        "",
    ]
    
    if not pairs:
        lines.append("✅ **유사도 40% 이상의 중복 문서가 발견되지 않았습니다.**")
    else:
        lines.append(f"총 **{len(pairs)}쌍**의 유사 문서가 검출되었습니다.")
        lines.append("")
        lines.append("| 순위 | 문서 A | 문서 B | 유사도 | 추천 조치 |")
        lines.append("| :--- | :--- | :--- | :---: | :--- |")
        for idx, pair in enumerate(pairs, 1):
            doc1 = pair["doc1"]
            doc2 = pair["doc2"]
            sim = pair["similarity"] * 100
            if sim >= 80:
                action = "**[필수 병합]** 내용 대부분 중복"
            elif sim >= 60:
                action = "**[병합 권장]** 개념 일치 및 설명 유사"
            else:
                action = "**[검토 요망]** 동일 주제 다른 기술"
            lines.append(f"| {idx} | [{os.path.basename(doc1)}](file://{os.path.join(BASE_DIR, doc1)}) | [{os.path.basename(doc2)}](file://{os.path.join(BASE_DIR, doc2)}) | **{sim:.1f}%** | {action} |")
            
    lines.append("\n---\n*보고서 작성: Antigravity AI*")
    content = "\n".join(lines)
    with open(REPORT_PATH, "w", encoding="utf-8") as f:
        f.write(content)
    return content

if __name__ == "__main__":
    docs = scan_vault()
    pairs = calculate_similarities(docs)
    write_report(pairs)
    print("Success")
