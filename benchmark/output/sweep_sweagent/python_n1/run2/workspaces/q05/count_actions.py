from collections import defaultdict

user_actions = defaultdict(set)

with open('user_actions.csv', 'r') as f:
    for line in f:
        line = line.strip()
        if line:
            parts = line.split(',')
            user = parts[0]
            action = parts[1]
            user_actions[user].add(action)

results = [(user, len(actions)) for user, actions in user_actions.items()]
results.sort(key=lambda x: -x[1])

for user, count in results:
    print(f"{user}: {count}")
