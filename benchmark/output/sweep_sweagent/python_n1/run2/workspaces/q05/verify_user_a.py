user_a_actions = set()

with open('user_actions.csv', 'r') as f:
    for line in f:
        line = line.strip()
        if line.startswith('user-a,'):
            action = line.split(',', 1)[1]
            user_a_actions.add(action)

print("User-a distinct actions:")
for action in sorted(user_a_actions):
    print(f"  - {action}")
print(f"\nTotal: {len(user_a_actions)}")
