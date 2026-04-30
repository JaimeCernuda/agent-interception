user_q_actions = set()

with open('user_actions.csv', 'r') as f:
    for line in f:
        line = line.strip()
        if line.startswith('user-q,'):
            action = line.split(',', 1)[1]
            user_q_actions.add(action)

print("User-q distinct actions:")
for action in sorted(user_q_actions):
    print(f"  - {action}")
print(f"\nTotal: {len(user_q_actions)}")
