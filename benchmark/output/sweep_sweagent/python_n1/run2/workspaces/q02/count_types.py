#!/usr/bin/env python3
from collections import defaultdict

# Read the types file
with open('types.txt', 'r') as f:
    types = f.readlines()

# Count occurrences
counts = defaultdict()
for event_type in types:
    event_type = event_type.strip()
    if event_type:
        counts[event_type] = counts.get(event_type, 0) + 1

# Sort by count descending
sorted_counts = sorted(counts.items(), key=lambda x: x[1], reverse=True)

# Print top 5
print("Top 5 event types in the second half (lines 20001-40000):")
print()
for i, (event_type, count) in enumerate(sorted_counts[:5], 1):
    print(f"{i}. {event_type}: {count}")
