import os
import re
from collections import defaultdict, deque

def extract_imports(filepath):
    """Extract imported modules from a Python file."""
    imports = set()
    try:
        with open(filepath, 'r') as f:
            content = f.read()
            # Match: import module_name
            for match in re.finditer(r'^import\s+(\w+)', content, re.MULTILINE):
                imports.add(match.group(1))
            # Match: from module_name import ...
            for match in re.finditer(r'^from\s+(\w+)\s+import', content, re.MULTILINE):
                imports.add(match.group(1))
    except Exception as e:
        print(f"Error reading {filepath}: {e}")
    return imports

def find_circular_imports():
    """Find all circular import cycles."""
    # Build import graph
    graph = defaultdict(set)
    files = {}
    
    # Scan all Python files
    for filename in os.listdir('.'):
        if filename.endswith('.py'):
            module_name = filename[:-3]  # Remove .py
            files[module_name] = filename
            imports = extract_imports(filename)
            graph[module_name] = imports
    
    # Find cycles using DFS
    cycles = []
    
    def dfs(node, path, visited, rec_stack):
        visited.add(node)
        rec_stack.add(node)
        path.append(node)
        
        for neighbor in graph[node]:
            if neighbor not in files:
                continue  # Skip external modules
                
            if neighbor not in visited:
                dfs(neighbor, path, visited, rec_stack)
            elif neighbor in rec_stack:
                # Found a cycle
                cycle_start = path.index(neighbor)
                cycle = path[cycle_start:] + [neighbor]
                cycles.append(cycle)
        
        path.pop()
        rec_stack.remove(node)
    
    visited = set()
    for node in graph:
        if node not in visited:
            dfs(node, [], visited, set())
    
    return cycles

cycles = find_circular_imports()
if cycles:
    print("Circular imports detected:")
    for i, cycle in enumerate(cycles, 1):
        print(f"\nCycle {i}: {' → '.join(cycle)}")
else:
    print("No circular imports detected.")
