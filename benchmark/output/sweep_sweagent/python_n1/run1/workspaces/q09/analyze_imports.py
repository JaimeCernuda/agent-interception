import re
import os
from collections import defaultdict

# Parse imports from Python files
imports = defaultdict(set)

for file in os.listdir('.'):
    if file.endswith('.py'):
        module_name = file[:-3]  # Remove .py
        
        with open(file, 'r') as f:
            content = f.read()
            # Find all import statements
            import_matches = re.findall(r'^import\s+(\w+)', content, re.MULTILINE)
            for match in import_matches:
                if match.endswith('.py'):
                    match = match[:-3]
                imports[module_name].add(match)

print("Import Graph:")
for module in sorted(imports.keys()):
    if imports[module]:
        print(f"  {module} → {', '.join(sorted(imports[module]))}")

# Find circular imports using DFS
def find_cycles():
    def dfs(node, path, rec_stack):
        rec_stack.add(node)
        
        for neighbor in imports[node]:
            if neighbor not in path:
                path.append(neighbor)
                result = dfs(neighbor, path, rec_stack)
                if result:
                    return result
                path.pop()
            elif neighbor in rec_stack:
                # Found cycle
                cycle_start = path.index(neighbor)
                return path[cycle_start:] + [neighbor]
        
        rec_stack.remove(node)
        return None
    
    unique_cycles = {}
    modules = sorted(imports.keys())
    
    for node in modules:
        result = dfs(node, [node], set())
        if result and len(result) > 1:
            # Normalize to canonical form (lexicographically smallest rotation)
            cycle_nodes = result[:-1]  # Remove duplicate last element
            min_rotation = min(
                tuple(cycle_nodes[i:] + cycle_nodes[:i]) 
                for i in range(len(cycle_nodes))
            )
            if min_rotation not in unique_cycles:
                unique_cycles[min_rotation] = min_rotation
    
    return list(unique_cycles.values())

cycles = find_cycles()

if cycles:
    print("\n✗ Circular imports detected:\n")
    for i, cycle in enumerate(cycles, 1):
        cycle_str = ' → '.join(cycle) + ' → ' + cycle[0]
        print(f"Cycle {i}: {cycle_str}")
else:
    print("\n✓ No circular imports detected")
