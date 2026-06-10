#!/bin/bash
# Backup consolidator_state.json with timestamp
DIR=$(dirname "$0")
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
mkdir -p "$DIR/backup"
cp "$DIR/consolidator_state.json" "$DIR/backup/consolidator_state.json.bak_$TIMESTAMP"
