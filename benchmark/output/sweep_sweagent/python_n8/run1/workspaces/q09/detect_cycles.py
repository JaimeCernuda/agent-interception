import os
import re
from collections import defaultdict, deque

# Parse imports from Python files
def get_imports(file_path):
    """Extract module imports from a Python file."""
    imports = set()
    try:
        with open(file_path, 'r') as f:
            content = f.read()
            # Match: import module_name
            for match in re.finditer(r'^\s*import\s+([a-zA-Z_][a-zA-Z0-9_]*)', content, re.MULTILINE):
                imports.add(match.group(1))
            # Match: from module_name import ...
            for match in re.finditer(r'^\s*from\s+([a-zA-Z_][a-zA-Z0-9_]*)\s+import', content, re.MULTILINE):
                imports.add(match.group(1))
    except Exception as e:
        print(f"Error reading {file_path}: {e}")
    return imports

# Build dependency graph
graph = defaultdict(set)
files = {}

for file in os.listdir('.'):
    if file.endswith('.py'):
        module_name = file[:-3]  # Remove .py extension
        files[module_name] = file
        imports = get_imports(file)
        # Filter to only local imports (files in this directory)
        local_imports = {imp for imp in imports if imp in [f[:-3] for f in os.listdir('.') if f.endswith('.py')]}
        graph[module_name] = local_imports

# Detect cycles using DFS
def find_cycles(graph):
    """Find all cycles in the dependency graph."""
    visited = set()
    rec_stack = set()
    cycles = []

    def dfs(node, path):
        visited.add(node)
        rec_stack.add(node)
        path.append(node)

        for neighbor in graph[node]:
            if neighbor not in visited:
                dfs(neighbor, path)
            elif neighbor in rec_stack:
                # Found a cycle
                cycle_start = path.index(neighbor)
                cycle = path[cycle_start:] + [neighbor]
                cycles.append(cycle)

        path.pop()
        rec_stack.remove(node)

    for node in graph:
        if node not in visited:
            dfs(node, [])

    return cycles

# Find and report cycles
cycles = find_cycles(graph)

print("=" * 60)
print("CIRCULAR IMPORT DETECTION REPORT")
print("=" * 60)
print()

if cycles:
    print(f"Found {len(cycles)} circular import cycle(s):\n")
    for i, cycle in enumerate(cycles, 1):
        print(f"Cycle {i}:")
        for j in range(len(cycle) - 1):
            print(f"  {cycle[j]} → {cycle[j+1]}")
        print()
else:
    print("No circular imports detected.")

print("Dependency Graph:")
for module, deps in sorted(graph.items()):
    if deps:
        print(f"  {module} → {', '.join(sorted(deps))}")
    else:
        print(f"  {module} → (no local imports)")
