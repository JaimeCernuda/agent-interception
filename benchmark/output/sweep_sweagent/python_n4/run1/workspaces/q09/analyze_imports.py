import os
import re
from collections import defaultdict, deque

def get_imports(filepath):
    """Extract import statements from a Python file"""
    imports = set()
    try:
        with open(filepath, 'r') as f:
            for line in f:
                # Match "import module_name"
                match = re.match(r'^\s*import\s+(\w+)', line)
                if match:
                    imports.add(match.group(1))
    except Exception as e:
        print(f"Error reading {filepath}: {e}")
    return imports

def find_cycles(graph):
    """Find all cycles in a directed graph using DFS"""
    visited = set()
    rec_stack = set()
    cycles = []
    
    def dfs(node, path):
        visited.add(node)
        rec_stack.add(node)
        path.append(node)
        
        for neighbor in graph.get(node, []):
            if neighbor not in visited:
                dfs(neighbor, path[:])
            elif neighbor in rec_stack:
                # Found a cycle
                cycle_start_idx = path.index(neighbor)
                cycle = path[cycle_start_idx:] + [neighbor]
                cycles.append(cycle)
        
        rec_stack.remove(node)
    
    for node in graph:
        if node not in visited:
            dfs(node, [])
    
    return cycles

# Build import graph
graph = defaultdict(list)
py_files = [f for f in os.listdir('.') if f.endswith('.py')]

for py_file in py_files:
    module_name = py_file[:-3]  # Remove .py
    imports = get_imports(py_file)
    for imp in imports:
        if imp + '.py' in py_files:  # Only include local imports
            graph[module_name].append(imp)

print("Import graph:")
for module, imports in sorted(graph.items()):
    print(f"  {module} → {imports}")

# Find cycles
cycles = find_cycles(graph)

print("\nCircular imports detected:")
if cycles:
    for cycle in cycles:
        print(f"  {' → '.join(cycle)}")
else:
    print("  None")
