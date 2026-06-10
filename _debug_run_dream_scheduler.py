#!/usr/bin/env python3
"""Run dream_scheduler manually and report"""
import sys
sys.path.insert(0, "/Users/bluesea/Applications/Mjauto/Scripts")
sys.path.insert(0, "/Users/bluesea/Applications/Mjauto/Scripts/modules")
from index_db import list_components
comps = list_components()
for c in comps:
    if c.get("component") == "dream_scheduler":
        print(f"dream_scheduler -> {c}")
