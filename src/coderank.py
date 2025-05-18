#!/usr/bin/env python3
import argparse
import ast
import os
import sys
from collections import defaultdict
import networkx as nx

# --- Helper Functions for Module Name and Import Resolution ---

def path_to_module_fqn(file_path, abs_repo_path):
    """
    Converts an absolute file path to a fully qualified Python module name.
    e.g., /path/to/repo/pkg/mod.py -> pkg.mod
    e.g., /path/to/repo/pkg/__init__.py -> pkg
    """
    # Ensure paths are absolute and normalized for reliable comparison
    file_path = os.path.normpath(os.path.abspath(file_path))
    abs_repo_path = os.path.normpath(os.path.abspath(abs_repo_path))

    if not file_path.startswith(abs_repo_path):
        # This might happen if symlinks point outside, or errors in path handling
        # print(f"Warning: File {file_path} is outside repo path {abs_repo_path}.")
        return None

    # Add trailing separator to repo path for clean relpath, unless it's the root itself
    repo_path_for_rel = abs_repo_path
    if abs_repo_path != os.path.dirname(abs_repo_path): # Not root like '/'
        repo_path_for_rel = abs_repo_path + os.path.sep
    
    try:
        relative_path = os.path.relpath(file_path, abs_repo_path)
    except ValueError: # if paths are on different drives on Windows
        # print(f"Warning: Cannot make {file_path} relative to {abs_repo_path}.")
        return None

    module_path_no_ext, _ = os.path.splitext(relative_path)
    parts = module_path_no_ext.split(os.path.sep)

    if not parts: # Should not happen if relpath is correct
        return None

    if parts[-1] == "__init__":
        parts.pop()
        if not parts:
            # This means it was __init__.py at the root of the repo.
            # Such a module is usually named after the repo directory itself when imported externally.
            # For internal analysis, it's hard to give it a simple dot-separated name from repo root.
            # We could use os.path.basename(abs_repo_path) but that might clash or be confusing.
            # Returning a special name or None. Let's use None and filter later.
            return None 
    
    return ".".join(parts) if parts else None


def resolve_relative_import(current_module_fqn, level, module_in_from_statement):
    """
    Resolves a relative import to a fully qualified name.
    current_module_fqn: FQN of the module doing the import (e.g., 'pkg.sub.mod').
    level: Import level (1 for '.', 2 for '..', etc.).
    module_in_from_statement: The module name string from the import (e.g., 'sibling' in 'from .sibling import X').
                              Can be None (e.g., 'from . import X').
    Returns: Fully qualified name of the imported module/package, or None if unresolvable.
    """
    if not current_module_fqn: # Cannot resolve relative if current module FQN is unknown/top-level script
        if level > 0 and module_in_from_statement: # e.g. from .foo import X in a script
             return module_in_from_statement # Assume it refers to a sibling module 'foo'
        return None

    path_parts = current_module_fqn.split('.')
    
    # Determine the base package for relative import
    # For 'from .foo' in 'pkg.sub.mod' (level 1), base is 'pkg.sub'
    # For 'from ..foo' in 'pkg.sub.mod' (level 2), base is 'pkg'
    # The number of parts to keep from the current FQN's package path
    # If current is 'a.b.c', level 1 -> use 'a.b'. Index is len - level = 3 - 1 = 2. Slice [:2] -> ['a','b']
    # If current is 'a.b.c', level 2 -> use 'a'. Index is len - level = 3 - 2 = 1. Slice [:1] -> ['a']
    # If current is 'c' (top-level module), level 1. len-level = 1-1 = 0. Slice [:0] -> []
    
    if level > len(path_parts): # Trying to go above the top-level package
        # print(f"Warning: Relative import level {level} too high for module {current_module_fqn}")
        return None
    
    # Base parts for constructing the resolved module name
    # Example: current_module_fqn = 'pkg.sub.mod', level = 1. path_parts[:-1] -> ['pkg', 'sub']
    # Example: current_module_fqn = 'pkg.sub.mod', level = 2. path_parts[:-2] -> ['pkg']
    # Example: current_module_fqn = 'mod' (top level), level = 1. path_parts[:-1] -> []
    base_module_parts = path_parts[:-level]

    if module_in_from_statement:
        # e.g., from .sibling_module import X  -> base_module_parts + [sibling_module]
        # e.g., from ..other_pkg.module import X -> base_module_parts + [other_pkg, module]
        # module_in_from_statement itself can be multi-part e.g. ..pkg.mod
        resolved_parts = base_module_parts + module_in_from_statement.split('.')
        return ".".join(resolved_parts)
    else:
        # e.g., from . import X  -> X is relative to the package defined by base_module_parts
        # The "module" is the package itself. If X is a submodule, it will be base_package.X
        # For now, we return the package. The caller handles iterating node.names.
        if not base_module_parts: # from . import X in a top-level script
             return None # The "current package" is ill-defined here.
        return ".".join(base_module_parts)


def get_imports_from_file(file_path, current_module_fqn):
    """
    Parses a Python file and extracts its imports as fully qualified names.
    """
    imports = set()
    try:
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()
        tree = ast.parse(content, filename=file_path)
    except Exception as e:
        # print(f"SyntaxError or other parsing error in {file_path}: {e}")
        return imports

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.add(alias.name)  # e.g., import os.path -> "os.path"
        
        elif isinstance(node, ast.ImportFrom):
            module_name_in_from = node.module  # 'X.Y' in 'from X.Y import Z' or 'foo' in 'from .foo import Z'
            level = node.level                 # 0 for absolute, 1 for '.', 2 for '..'

            if level > 0:  # Relative import
                resolved_base = resolve_relative_import(current_module_fqn, level, module_name_in_from)
                if not resolved_base: # Could not resolve (e.g. too many levels up, or top-level from .)
                    continue

                if module_name_in_from: # e.g. from .foo import bar -> resolved_base is current_pkg.foo
                    imports.add(resolved_base)
                else: # e.g. from . import foo, bar -> resolved_base is current_pkg
                      # imports are current_pkg.foo, current_pkg.bar
                    for alias in node.names:
                        imports.add(f"{resolved_base}.{alias.name}")
            
            else:  # Absolute import: from module import name1, name2
                if module_name_in_from:
                    # Standard interpretation: dependency is on 'module_name_in_from'
                    # e.g. from A.B import C -> dependency on A.B
                    # e.g. from A import B -> dependency on A. (If B is submodule A.B, it's more complex)
                    # For this script, we'll simplify: 'from X import Y' links to X.
                    imports.add(module_name_in_from)
    return imports

# --- Core Logic ---

def discover_python_files(repo_path):
    """Finds all .py files in the repository."""
    py_files = []
    abs_repo_path = os.path.abspath(repo_path)
    for root, _, files in os.walk(abs_repo_path):
        for file in files:
            if file.endswith(".py"):
                py_files.append(os.path.join(root, file))
    return py_files

def analyze_repo(repo_path, external_modules_str, 
                 damping_factor, weight_internal, 
                 weight_external_import, weight_external_dependency,
                 output_file_path, top_n_arg):
    """
    Main analysis function.
    """
    abs_repo_path = os.path.abspath(repo_path)
    if not os.path.isdir(abs_repo_path):
        print(f"Error: Repository path {abs_repo_path} not found or not a directory.")
        return

    specified_external_modules = set(e.strip() for e in external_modules_str.split(',') if e.strip())
    
    print(f"Discovering Python files in {abs_repo_path}...")
    all_py_files = discover_python_files(abs_repo_path)
    if not all_py_files:
        print("No Python files found.")
        return

    module_map = {} # FQN -> file_path
    file_to_fqn = {} # file_path -> FQN
    internal_module_fqns = set()

    print("Mapping files to module names...")
    for f_path in all_py_files:
        fqn = path_to_module_fqn(f_path, abs_repo_path)
        if fqn: # path_to_module_fqn can return None for e.g. repo_root/__init__.py
            module_map[fqn] = f_path
            file_to_fqn[f_path] = fqn
            internal_module_fqns.add(fqn)
    
    print(f"Found {len(internal_module_fqns)} unique internal modules.")

    # Initialize graphs
    G_imports = nx.DiGraph() # A imports B: A -> B
    G_imported_by = nx.DiGraph() # A imports B: B -> A (for PageRank "outgoing")

    all_graph_nodes = set(internal_module_fqns)
    for ext_mod in specified_external_modules:
        all_graph_nodes.add(ext_mod) # Add external modules as potential nodes

    # Add all potential nodes to ensure PageRank considers them even if some have no edges
    for node_fqn in all_graph_nodes:
        G_imports.add_node(node_fqn)
        G_imported_by.add_node(node_fqn)

    print("Parsing imports and building dependency graphs...")
    for f_path in all_py_files:
        current_fqn = file_to_fqn.get(f_path)
        if not current_fqn:
            # print(f"Skipping {f_path}, could not determine its module FQN.")
            continue

        imported_fqns = get_imports_from_file(f_path, current_fqn)
        
        for imported_fqn_full in imported_fqns:
            target_node_fqn = None
            is_external = False
            weight_to = weight_internal
            weight_from = weight_internal # Weight for the reverse link

            # Check if it's a specified external module
            # e.g. import sklearn.linear_model -> target is "sklearn"
            imported_root = imported_fqn_full.split('.')[0]
            if imported_root in specified_external_modules:
                target_node_fqn = imported_root
                is_external = True
                weight_to = weight_external_import
                weight_from = weight_external_dependency
            # Check if it's an internal module
            elif imported_fqn_full in internal_module_fqns:
                target_node_fqn = imported_fqn_full
            # else: # It's a stdlib or unspecified external module, ignore for now
            #     # print(f"  {current_fqn} imports {imported_fqn_full} (ignored or stdlib)")
            #     pass

            if target_node_fqn:
                # print(f"  {current_fqn} -> {target_node_fqn} ({'ext' if is_external else 'int'})")
                if current_fqn != target_node_fqn: # Avoid self-loops affecting rank artificially here
                    G_imports.add_edge(current_fqn, target_node_fqn, weight=weight_to)
                    G_imported_by.add_edge(target_node_fqn, current_fqn, weight=weight_from)
    
    if not G_imports.nodes() or not G_imported_by.nodes():
        print("Graphs are empty, cannot compute PageRank.")
        return

    print("Calculating PageRank (for being imported)...")
    try:
        # Personalization: give external modules some base importance if desired
        # For now, relying on structure and edge weights
        personalization_vec_imports = {ext_mod: 0.1 for ext_mod in specified_external_modules} # Small boost
        # Normalize personalization if used:
        # total_pers = sum(personalization_vec_imports.values())
        # if total_pers > 0:
        #    personalization_vec_imports = {k: v / total_pers for k, v in personalization_vec_imports.items()}
        # else: personalization_vec_imports = None

        pagerank_being_imported = nx.pagerank(G_imports, alpha=damping_factor, weight='weight', tol=1.0e-8, max_iter=200)
                                            #   personalization=personalization_vec_imports if personalization_vec_imports else None)
    except nx.PowerIterationFailedConvergence:
        print("PageRank (being_imported) failed to converge. Using uniform ranks.")
        pagerank_being_imported = {node: 1.0 / len(G_imports) for node in G_imports.nodes()}


    print("Calculating PageRank (for importing others)...")
    try:
        # For G_imported_by, an internal module gets rank if external modules "point" to it
        # (meaning the internal module imports the external one)
        # Personalization here could mean certain external modules are more "valuable" to import from.
        personalization_vec_imported_by = {ext_mod: 0.1 for ext_mod in specified_external_modules}
        # total_pers_ib = sum(personalization_vec_imported_by.values())
        # if total_pers_ib > 0:
        #    personalization_vec_imported_by = {k: v / total_pers_ib for k,v in personalization_vec_imported_by.items()}
        # else: personalization_vec_imported_by = None

        pagerank_importing_others = nx.pagerank(G_imported_by, alpha=damping_factor, weight='weight', tol=1.0e-8, max_iter=200)
                                              # personalization=personalization_vec_imported_by if personalization_vec_imported_by else None)

    except nx.PowerIterationFailedConvergence:
        print("PageRank (importing_others) failed to converge. Using uniform ranks.")
        pagerank_importing_others = {node: 1.0 / len(G_imported_by) for node in G_imported_by.nodes()}


    print("\n--- CodeRank Results ---")
    code_ranks = {}
    for module_fqn in internal_module_fqns:
        score_being_imported = pagerank_being_imported.get(module_fqn, 0)
        score_importing_others = pagerank_importing_others.get(module_fqn, 0)
        # The sum represents both aspects of relevance
        code_ranks[module_fqn] = score_being_imported + score_importing_others

    if not code_ranks:
        print("No internal modules to rank or ranks are all zero.")
        return

    # Sort by rank descending
    sorted_ranks = sorted(code_ranks.items(), key=lambda item: item[1], reverse=True)

    # --- Console Output --- 
    print("\n--- CodeRank Results ---")
    if not sorted_ranks:
        # This case should ideally be caught by the 'if not code_ranks' check earlier,
        # but as a fallback:
        print("No modules to display.")
    else:
        num_to_display_console = len(sorted_ranks)
        if top_n_arg > 0:
            num_to_display_console = min(top_n_arg, len(sorted_ranks))

        display_ranks_console = sorted_ranks[:num_to_display_console]
        
        console_max_name_len = 0
        if display_ranks_console:
            console_max_name_len = max(len(name) for name, _ in display_ranks_console)
        
        header_module_console = 'Module'
        header_score_console = 'CodeRank Score'
        # Ensure column width is at least header length
        if console_max_name_len < len(header_module_console):
            console_max_name_len = len(header_module_console)

        if top_n_arg > 0 and len(sorted_ranks) > top_n_arg and display_ranks_console:
            print(f"(Displaying Top {num_to_display_console} of {len(sorted_ranks)} modules on console)")
        
        if display_ranks_console:
            print(f"{header_module_console.ljust(console_max_name_len)} | {header_score_console}")
            print(f"{'-' * console_max_name_len} | {'-' * len(header_score_console)}")
            for module_fqn, rank in display_ranks_console:
                print(f"{module_fqn.ljust(console_max_name_len)} | {rank:.6f}")
        elif top_n_arg == 0 : # Specifically if top_n was set to 0, meaning don't print ranks
            print("(Rank display to console suppressed by --top_n 0)")
        # If display_ranks_console is empty due to no ranks, the earlier check handles it.

    # --- File Output --- 
    if output_file_path:
        print(f"\nWriting results to {output_file_path}...")
        try:
            with open(output_file_path, 'w', encoding='utf-8') as f_out:
                f_out.write("--- CodeRank Results ---\n")
                
                if not sorted_ranks:
                    f_out.write("No modules to rank.\n")
                else:
                    file_header_module = 'Module'
                    file_header_score = 'CodeRank Score'
                    
                    file_max_name_len = 0
                    if sorted_ranks: # Calculate max_name_len based on ALL ranks for file output
                        file_max_name_len = max(len(name) for name, _ in sorted_ranks)
                    
                    if file_max_name_len < len(file_header_module):
                        file_max_name_len = len(file_header_module)

                    f_out.write(f"{file_header_module.ljust(file_max_name_len)} | {file_header_score}\\n")
                    f_out.write(f"{'-' * file_max_name_len} | {'-' * len(file_header_score)}\\n")
                    for module_fqn, rank in sorted_ranks: # Write all ranks to file
                        f_out.write(f"{module_fqn.ljust(file_max_name_len)} | {rank:.6f}\\n")

                # Copy top_n_arg files to the output file
                if top_n_arg > 0 and sorted_ranks:
                    f_out.write(f"\n\n--- Top {top_n_arg} Ranked File Contents ---\n")
                    
                    files_copied_count = 0
                    for module_fqn, rank_score in sorted_ranks:
                        if files_copied_count >= top_n_arg:
                            break
                        
                        file_path_to_copy = module_map.get(module_fqn)
                        if file_path_to_copy: 
                            f_out.write(f"\n\n--- START FILE ({module_fqn} | Rank: {rank_score:.6f}): {file_path_to_copy} ---\n\n")
                            try:
                                with open(file_path_to_copy, 'r', encoding='utf-8', errors='ignore') as f_module:
                                    f_out.write(f_module.read())
                                f_out.write(f"\n--- END FILE: {file_path_to_copy} ---\n")
                                files_copied_count += 1
                            except Exception as e:
                                f_out.write(f"\n!!! Error reading file {file_path_to_copy}: {e} !!!\n")
                    print(f"Successfully wrote results and top {files_copied_count} file contents to {output_file_path}")
                elif top_n_arg > 0: # No sorted_ranks but top_n_arg > 0
                    f_out.write(f"\n\n--- Top {top_n_arg} Ranked File Contents ---\n")
                    f_out.write("No ranked files to copy.\n")
                    print(f"Successfully wrote results to {output_file_path}. No files to copy as no modules were ranked.")
                else: # top_n_arg <= 0
                    print(f"Successfully wrote results to {output_file_path}. File content copying not requested (top_n = {top_n_arg}).")

        except IOError as e:
            print(f"Error writing to output file {output_file_path}: {e}")

    # Optional: print details about external modules influence
    # print("\n--- External Module Ranks (for reference) ---")
    # for ext_mod in specified_external_modules:
    #     pr_imp = pagerank_being_imported.get(ext_mod, 0)
    #     pr_imp_by = pagerank_importing_others.get(ext_mod, 0)
    #     print(f"{ext_mod.ljust(max_name_len)} | PR_imports: {pr_imp:.6f}, PR_imported_by: {pr_imp_by:.6f}")


def main():
    parser = argparse.ArgumentParser(description="Calculate CodeRank for Python files in a repository.")
    parser.add_argument("repo_path", type=str, help="Path to the Python repository.")
    parser.add_argument("--external_modules", type=str, default="numpy,pandas,sklearn,torch,tensorflow,requests,django,flask",
                        help="Comma-separated list of external modules to consider (e.g., sklearn,numpy).")
    parser.add_argument("--damping_factor", type=float, default=0.85,
                        help="Damping factor (alpha) for PageRank. (Default: 0.85)")
    parser.add_argument("--weight_internal", type=float, default=1.0,
                        help="Weight for internal import links. (Default: 1.0)")
    parser.add_argument("--weight_external_import", type=float, default=0.5,
                        help="Weight for links FROM internal modules TO external modules (A imports E). (Default: 0.5)")
    parser.add_argument("--weight_external_dependency", type=float, default=0.5,
                        help="Weight for links TO internal modules FROM external modules (E 'links back' to A because A imports E). (Default: 0.5)")
    parser.add_argument("--output_file", type=str, default="coderank_results.txt",
                        help="File to write results to. (Default: coderank_results.txt)")
    parser.add_argument("--top_n", type=int, default=20,
                        help="Number of top modules to print to console and whose contents to copy to the output file. (Default: 20)")
    args = parser.parse_args()

    analyze_repo(args.repo_path, args.external_modules,
                 args.damping_factor, args.weight_internal,
                 args.weight_external_import, args.weight_external_dependency,
                 args.output_file, args.top_n)

if __name__ == "__main__":
    main()