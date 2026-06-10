#!/bin/bash
echo "🧹 macOS 메모리 최적화를 시작합니다..."
echo "이 작업은 사용되지 않는 파일 캐시(File-backed pages)를 확보하여"
echo "물리적 가용 메모리를 늘려줍니다."
echo "관리자 권한이 필요합니다."
sudo purge
echo "✅ 메모리 최적화 완료! vm_stat 결과를 확인하세요:"
vm_stat | grep -E "^Pages free|^File-backed pages"
