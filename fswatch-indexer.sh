#!/bin/bash
# fswatch-indexer.sh — fswatch wrapper for launchd
# Monitored by: com.bluesea.fswatch-indexer.plist
#
# Runs fswatch -0 and pipes changed file paths to update_index.py.

FSWATCH="/opt/homebrew/bin/fswatch"
UPDATE_INDEX="/Users/bluesea/Applications/Mjauto/Scripts/update_index.py"
WIKI_STAMPER="/Users/bluesea/Applications/Mjauto/Scripts/wiki_auto_stamper.py"
PYTHON="/usr/bin/python3"

WATCH_DIRS=(
    "/Users/bluesea/Applications/Mjobsidian/wiki"
    "/Users/bluesea/.hermes/governance"
    "/Users/bluesea/Applications/Mjauto/Scripts/handlers"
    "/Users/bluesea/Applications/Mjauto/Scripts/modules"
)

LOGFILE="/Users/bluesea/Applications/Mjauto/Scripts/logs/fswatch-indexer.log"

{
    "$FSWATCH" -0 \
        --event Created \
        --event Updated \
        --event Renamed \
        --exclude '.*/\.#' \
        --exclude '.*__pycache__' \
        --exclude '.*/\.obsidian/.*' \
        --exclude '.*/\.smart-env/.*' \
        --exclude '.*/99_Archive/.*' \
        "${WATCH_DIRS[@]}" \
    | while IFS= read -r -d '' changed_file; do
        if [ -n "$changed_file" ]; then
            "$PYTHON" "$UPDATE_INDEX" --path "$changed_file" >> "$LOGFILE" 2>&1
            # .md 파일이면 태그(최대8개)·링크제거·타임스탬프 자동 적용
            if [[ "$changed_file" == *.md ]]; then
                "$PYTHON" "$WIKI_STAMPER" "$changed_file" >> "$LOGFILE" 2>&1
            fi
        fi
    done
} >> "$LOGFILE" 2>&1 &

wait
