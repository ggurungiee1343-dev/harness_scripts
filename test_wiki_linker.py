import sys
import os
from pathlib import Path

# 모듈 경로 추가
sys.path.append(str(Path(__file__).parent / "modules"))

from wiki_linker import WikiLinker

def run_tests():
    print("🧪 [WikiLinker 테스트] 자율 검증 시작...")
    
    linker = WikiLinker(vault_path="/Users/bluesea/Applications/Mjobsidian")
    
    # 1. 기존 위키 노트 수집 테스트
    titles = linker.get_existing_note_titles()
    print(f"  - 수집된 노트 개수: {len(titles)}")
    print("  - 'Graphify' 포함 노트 목록:", [t for t in titles if "Graphify" in t])
    assert len(titles) > 0, "❌ 위키 노트 수집 실패: 등록된 노트가 하나도 없습니다."
    
    # "Graphify_사용_가이드"가 목록에 포함되어 있는지 확인
    assert any("Graphify" in t for t in titles), "❌ 'Graphify_사용_가이드' 등의 노트가 수집되지 않았습니다."
    print("  - [성공] 노트 제목 수집 확인 완료.")

    # 2. 텍스트 변환 테스트
    # "Graphify 사용 가이드" 라는 띄어쓰기 텍스트가 "Graphify_사용_가이드.md" 파일명과 매칭되어 변환되는지 테스트
    sample_text = (
        "---\ntags: [test]\n---\n"
        "이것은 Graphify 사용 가이드 문서를 참조합니다.\n"
        "코드 블록 내부의 `Graphify 사용 가이드` 또는 ```\nGraphify 사용 가이드\n``` 는 변환되면 안 됩니다.\n"
        "이미 [[Graphify_사용_가이드]] 로 연결된 것도 중복으로 치환되면 안 됩니다.\n"
    )
    
    print("  - 변환 전 테스트 텍스트 준비 완료.")
    result = linker.link_text(sample_text)
    print("  - 변환 결과:\n", result)
    
    # 검증
    # 1) 일반 텍스트 내의 "Graphify 사용 가이드"는 링크로 치환되어야 함
    assert "[[Graphify_사용_가이드|Graphify 사용 가이드]]" in result, "❌ 일반 텍스트 내 키워드 치환 실패"
    
    # 2) 인라인 코드 내 "Graphify 사용 가이드"는 유지되어야 함
    assert "`Graphify 사용 가이드`" in result, "❌ 인라인 코드 블록 손상됨"
    
    # 3) 멀티라인 코드 내 "Graphify 사용 가이드"는 유지되어야 함
    assert "```\nGraphify 사용 가이드\n```" in result, "❌ 멀티라인 코드 블록 손상됨"
    
    # 4) 이미 위키 링크인 "[[Graphify_사용_가이드]]"는 원래대로 유지되어야 함 (중복 치환 방지)
    assert "[[Graphify_사용_가이드]]" in result, "❌ 기존 위키 링크가 중복으로 꼬여 치환됨"
    
    print("🎉 [WikiLinker 테스트] 모든 단위 테스트 성공! (All assertions passed)")

if __name__ == "__main__":
    run_tests()
