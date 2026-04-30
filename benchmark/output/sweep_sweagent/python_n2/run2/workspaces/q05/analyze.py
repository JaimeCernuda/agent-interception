import sys
from collections import defaultdict

# Read pairs from stdin and count distinct actions per user
user_actions = defaultdict(set)

with open('pairs.txt', 'r') as f:
    for line in f:
        line = line.strip()
        if not line:
            continue
        # Parse: user=user-q action=update_user
        parts = line.split(' ')
        user = parts[0].split('=')[1]
        action = parts[1].split('=')[1]
        user_actions[user].add(action)

# Find user with most distinct actions
max_user = None
max_count = 0
for user in sorted(user_actions.keys()):
    count = len(user_actions[user])
    if count > max_count:
        max_count = count
        max_user = user

print(f"User with most distinct actions: {max_user}")
print(f"Number of distinct actions: {max_count}")
print(f"\nActions performed by {max_user}:")
for action in sorted(user_actions[max_user]):
    print(f"  - {action}")
