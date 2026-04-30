import ast
import os
from collections import defaultdict, deque

# Build import graph
imports = defaultdict(set)
all_modules = set()

for filename in os.listdir('.'):
    if filename.endswith('.py'):
        module_name = filename[:-3]
        all_modules.add(module_name)
        
        with open(filename, 'r') as f:
            try:
                tree = ast.parse(f.read())
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        for alias in node.names:
                            imports[module_name].add(alias.name)
                    elif isinstance(node, ast.ImportFrom):
                        if node.module:
                            imports[module_name].add(node.module)
            except:
                pass

# Find cycles using DFS
def find_cycles(graph):
    visited = set()
    rec_stack = set()
    cycles = []
    
    def dfs(node, path):
        visited.add(node)
        rec_stack.add(node)
        path.append(node)
        
        for neighbor in graph.get(node, set()):
            if neighbor not in all_modules:
                continue
            if neighbor not in visited:
                dfs(neighbor, path[:])
            elif neighbor in rec_stack:
                # Found a cycle
                cycle_start = path.index(neighbor)
                cycle = path[cycle_start:] + [neighbor]
                cycles.append(cycle)
        
        rec_stack.remove(node)
    
    for node in all_modules:
        if node not in visited:
            dfs(node, [])
    
    return cycles

cycles = find_cycles(imports)

if cycles:
    print("Circular imports detected:\n")
    for cycle in cycles:
        print(" → ".join(cycle))
else:
    print("No circular imports found.")
