#!/bin/bash
# fswatch-indexer.sh — fswatch wrapper for launchd
# Monitored by: com.bluesea.fswatch-indexer.plist
#
# Runs fswatch -0 and pipes changed file paths to update_index.py.

FSWATCH="/opt/homebrew/bin/fswatch"
UPDATE_INDEX="/Users/bluesea/Applications/Mjauto/Scripts/update_index.py"
PYTHON="/usr/bin/python3"

WATCH_DIRS=(
    "/Users/bluesea/Applications/Mjobsidian/wiki/00_Meta"
    "/Users/bluesea/.hermes/governance"
    "/Users/bluesea/Applications/Mjauto/Scripts/handlers"
    "/Users/bluesea/Applications/Mjauto/Scripts/modules"
    "/Users/bluesea/Applications/Mjobsidian/wiki/10_AI_Automation"
)

LOGFILE="/Users/bluesea/Applications/Mjauto/Scripts/fswatch-indexer.log"

{
    "$FSWATCH" -0 \
        --event Created \
        --event Updated \
        --event Renamed \
        --exclude '.*/\.#' \
        --exclude '.*__pycache__' \
        "${WATCH_DIRS[@]}" \
    | while IFS= read -r -d '' changed_file; do
        if [ -n "$changed_file" ]; then
            "$PYTHON" "$UPDATE_INDEX" --path "$changed_file" >> "$LOGFILE" 2>&1
        fi
    done
} >> "$LOGFILE" 2>&1 &

wait
