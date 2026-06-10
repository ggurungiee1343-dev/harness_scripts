import os
import shutil
import re
from vault_scanner import scan_vault, calculate_similarities

BASE_DIR = "/Users/bluesea/Applications/Mjobsidian"
BACKUP_ROOT = os.path.join(BASE_DIR, "wiki/99_Archive/Backup_20260520")

def inject_alias(master_full_path, dup_title):
    try:
        with open(master_full_path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()
    except Exception as e:
        print(f"❌ 마스터 파일 읽기 실패: {master_full_path} ({e})")
        return False

    # 이미 동일한 별칭이 들어있는지 체크
    if dup_title in content:
        return True

    # Frontmatter 처리
    if content.startswith("---"):
        # 기존 frontmatter 영역 찾기
        parts = content.split("---", 2)
        if len(parts) >= 3:
            fm = parts[1]
            body = parts[2]
            
            # aliases 행이 있는지 확인
            alias_match = re.search(r"aliases:\s*\[(.*?)\]", fm)
            if alias_match:
                existing = alias_match.group(1).strip()
                if dup_title not in existing:
                    new_aliases = f"aliases: [{existing}, {dup_title}]" if existing else f"aliases: [{dup_title}]"
                    new_fm = re.sub(r"aliases:\s*\[.*?\]", new_aliases, fm)
                else:
                    new_fm = fm
            elif "aliases:" in fm:
                # aliases: 단일 형태로 적힌 경우
                new_fm = fm + f"\n  - {dup_title}"
            else:
                # aliases 자체가 없는 경우
                new_fm = fm.rstrip("\n") + f"\naliases: [{dup_title}]\n"
                
            new_content = f"---{new_fm}---{body}"
        else:
            new_content = f"---\naliases: [{dup_title}]\n---\n" + content
    else:
        new_content = f"---\naliases: [{dup_title}]\n---\n" + content

    try:
        with open(master_full_path, "w", encoding="utf-8") as f:
            f.write(new_content)
        return True
    except Exception as e:
        print(f"❌ 마스터 파일 별칭 주입 실패: {master_full_path} ({e})")
        return False

def make_redirect_file(dup_full_path, master_title):
    redirect_content = f"""# 🚚 통합 리다이렉트: [[{master_title}]]

*이 노트는 2026-05-20에 [[{master_title}]] 노트와 통합되었습니다. 기존 상세 본문은 보관소 백업 아카이브에서 안전하게 조회하실 수 있습니다.*

---
*통합본 바로가기: [[{master_title}]]*
"""
    try:
        with open(dup_full_path, "w", encoding="utf-8") as f:
            f.write(redirect_content)
        return True
    except Exception as e:
        print(f"❌ 리다이렉트 파일 작성 실패: {dup_full_path} ({e})")
        return False

def run_deduplication():
    docs = scan_vault()
    pairs = calculate_similarities(docs)
    
    # 유사도 90% 이상인 것만 필터링
    high_sim_pairs = [p for p in pairs if p["similarity"] >= 0.90]
    
    if not high_sim_pairs:
        print("✅ 병합할 유사도 90% 이상의 중복 문서가 없습니다.")
        return
        
    print(f"🔄 총 {len(high_sim_pairs)}쌍의 중복 문서 안전 병합을 시작합니다...")
    os.makedirs(BACKUP_ROOT, exist_ok=True)
    
    processed_count = 0
    
    for pair in high_sim_pairs:
        doc1 = pair["doc1"]
        doc2 = pair["doc2"]
        sim = pair["similarity"]
        
        # 1. 마스터(Master)와 복제본(Duplicate) 구분 결정
        # wiki 폴더 내의 노트를 마스터로 선호
        if doc1.startswith("wiki/") and not doc2.startswith("wiki/"):
            master_rel = doc1
            dup_rel = doc2
        elif doc2.startswith("wiki/") and not doc1.startswith("wiki/"):
            master_rel = doc2
            dup_rel = doc1
        else:
            # 둘 다 wiki 내이거나 둘 다 외부에 있다면 경로가 더 짧은 쪽을 마스터로 설정
            if len(doc1) <= len(doc2):
                master_rel = doc1
                dup_rel = doc2
            else:
                master_rel = doc2
                dup_rel = doc1
                
        master_full = os.path.join(BASE_DIR, master_rel)
        dup_full = os.path.join(BASE_DIR, dup_rel)
        
        master_title = os.path.basename(master_rel).replace(".md", "")
        dup_title = os.path.basename(dup_rel).replace(".md", "")
        
        # 파일이 실제로 존재하지 않으면 스킵
        if not os.path.exists(master_full) or not os.path.exists(dup_full):
            continue
            
        print(f"🔹 병합 작업 [{pair['similarity']*100:.1f}%]: {dup_rel} ➔ {master_rel}")
        
        # 2. 안전 백업 생성
        backup_master = os.path.join(BACKUP_ROOT, master_rel)
        backup_dup = os.path.join(BACKUP_ROOT, dup_rel)
        
        os.makedirs(os.path.dirname(backup_master), exist_ok=True)
        os.makedirs(os.path.dirname(backup_dup), exist_ok=True)
        
        try:
            shutil.copy2(master_full, backup_master)
            shutil.copy2(dup_full, backup_dup)
        except Exception as e:
            print(f"❌ 백업 복사 실패: {e}")
            continue
            
        # 3. 마스터 파일에 별칭(aliases) 추가
        if not inject_alias(master_full, dup_title):
            continue
            
        # 4. 복제 파일 내용을 리다이렉트로 변경 (삭제 차단)
        if not make_redirect_file(dup_full, master_title):
            continue
            
        processed_count += 1
        
    print(f"🏁 안전 병합 완료! 총 {processed_count}개의 중복 문서가 리다이렉트 및 백업 처리되었습니다.")

if __name__ == "__main__":
    run_deduplication()
