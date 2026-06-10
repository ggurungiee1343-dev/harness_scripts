import os
import shutil

def setup_family_member(name, token):
    print(f"🚀 [{name}] 님을 위한 전용 맥봇(MacBot) 구축을 시작합니다...")

    # 1. 전용 옵시디언 볼트 생성
    base_vault = "/Users/bluesea/Applications/Mjobsidian"
    new_vault = f"/Users/bluesea/Applications/Mjobsidian_{name}"
    
    if not os.path.exists(new_vault):
        os.makedirs(os.path.join(new_vault, "wiki", "00_Meta"))
        os.makedirs(os.path.join(new_vault, "wiki", "10_Daily"))
        os.makedirs(os.path.join(new_vault, "Clippings"))
        
        # 기본 memory.md 및 hot.md 생성
        with open(os.path.join(new_vault, "wiki", "00_Meta", "memory.md"), "w") as f:
            f.write(f"# 🧠 {name}님의 전용 기억 저장소\n")
        with open(os.path.join(new_vault, "wiki", "00_Meta", "hot.md"), "w") as f:
            f.write(f"# 🔥 {name}님의 핫토픽\n")
        print(f"✅ 전용 지식 베이스(Vault) 생성 완료: {new_vault}")
    else:
        print(f"⚠️ 이미 존재하는 볼트입니다: {new_vault}")

    # 2. 전용 스크립트 복제
    base_script_dir = "/Users/bluesea/Applications/Mjauto/Scripts/MacBot"
    new_script_dir = f"/Users/bluesea/Applications/Mjauto/Scripts/MacBot_{name}"
    
    if not os.path.exists(new_script_dir):
        shutil.copytree(base_script_dir, new_script_dir)
        print(f"✅ 전용 스크립트 폴더 생성 완료: {new_script_dir}")
    else:
        print(f"⚠️ 이미 존재하는 스크립트 폴더입니다: {new_script_dir}")

    # 3. 전용 Config 파일 생성
    config_content = f"""import os

# {name} 전용 설정
MACBOT_TOKEN = "{token}"
ALLOWED_ID = None # 텔레그램 ID를 여기에 입력하세요
WORK_SPACE = "{new_vault}"
MEM_THRESHOLD_GB = 0.5
"""
    with open(os.path.join(new_script_dir, "config_local.py"), "w") as f:
        f.write(config_content)
    print(f"✅ 전용 설정 파일 생성 완료: {new_script_dir}/config_local.py")

    # 4. macbot.py 수정 (config_local 로드하도록)
    macbot_path = os.path.join(new_script_dir, "macbot.py")
    with open(macbot_path, "r") as f:
        content = f.read()
    
    content = content.replace("import config", "import config_local as config")
    with open(macbot_path, "w") as f:
        f.write(content)
    
    print(f"\n🎉 구축 완료! 이제 아래 명령어로 {name}님의 봇을 실행할 수 있습니다.")
    print(f"python3 {macbot_path}")

if __name__ == "__main__":
    import sys
    if len(sys.argv) < 3:
        print("사용법: python3 setup_family_bot.py [가족이름] [텔레그램토큰]")
        sys.exit(1)
    
    setup_family_member(sys.argv[1], sys.argv[2])
