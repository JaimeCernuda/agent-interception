import os
import re
from collections import defaultdict, deque

def find_imports(filepath):
    """Extract import statements from a Python file."""
    imports = set()
    try:
        with open(filepath, 'r') as f:
            content = f.read()
            # Find direct imports of modules in this project
            for match in re.finditer(r'^\s*import\s+(mod_[a-z])', content, re.MULTILINE):
                imports.add(match.group(1) + '.py')
            for match in re.finditer(r'^\s*from\s+(mod_[a-z])', content, re.MULTILINE):
                imports.add(match.group(1) + '.py')
    except:
        pass
    return imports

def detect_cycles(graph):
    """Detect cycles using DFS."""
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
                cycle_start = path.index(neighbor)
                cycle = path[cycle_start:] + [neighbor]
                cycles.append(cycle)
        
        rec_stack.remove(node)
    
    for node in graph:
        if node not in visited:
            dfs(node, [])
    
    return cycles

# Build dependency graph
graph = defaultdict(list)
py_files = [f for f in os.listdir('.') if f.endswith('.py')]

for py_file in py_files:
    imports = find_imports(py_file)
    for imp in imports:
        graph[py_file].append(imp)

# Detect cycles
cycles = detect_cycles(graph)

if cycles:
    print("Found circular imports:\n")
    for i, cycle in enumerate(cycles, 1):
        print(f"Cycle {i}:")
        print(" → ".join(cycle))
        print()
else:
    print("No circular imports detected.")
