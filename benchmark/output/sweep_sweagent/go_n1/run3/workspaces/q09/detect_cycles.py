import re
import os
from collections import defaultdict, deque

def get_imports(filepath):
    """Extract direct imports from a Python file."""
    imports = set()
    try:
        with open(filepath, 'r') as f:
            content = f.read()
            # Match: import module_name or from module_name import ...
            pattern = r'^\s*(?:from\s+(\w+)|import\s+(\w+))'
            for match in re.finditer(pattern, content, re.MULTILINE):
                module = match.group(1) or match.group(2)
                imports.add(module)
    except Exception as e:
        print(f"Error reading {filepath}: {e}")
    return imports

def find_cycles(graph):
    """Find all cycles in a directed graph using DFS."""
    visited = set()
    rec_stack = set()
    cycles = []
    
    def dfs(node, path):
        visited.add(node)
        rec_stack.add(node)
        path.append(node)
        
        for neighbor in graph.get(node, []):
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

# Find all Python files
py_files = {}
for file in os.listdir('.'):
    if file.endswith('.py'):
        module_name = file[:-3]  # Remove .py extension
        py_files[module_name] = file

# Build import graph
graph = defaultdict(set)
for module, filepath in py_files.items():
    imports = get_imports(filepath)
    for imp in imports:
        if imp in py_files:  # Only consider internal imports
            graph[module].add(imp)

print("Import Graph:")
for module in sorted(graph.keys()):
    if graph[module]:
        print(f"  {module} -> {sorted(graph[module])}")

# Find cycles
cycles = find_cycles(graph)

if cycles:
    print("\n\nCircular Imports Detected:")
    seen_cycles = set()
    for cycle in cycles:
        # Normalize cycle to avoid duplicates (same cycle starting from different points)
        cycle_str = ' -> '.join(cycle)
        if cycle_str not in seen_cycles:
            seen_cycles.add(cycle_str)
            print(f"  {cycle_str}")
else:
    print("\n\nNo circular imports detected.")
