#!/usr/bin/env python3
import argparse
import ast
import os
import sys
import re # Added for Markdown analysis
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

# --- New Function: Python Symbol Extraction ---
def extract_python_symbols(file_path, current_module_fqn, abs_repo_path, python_symbols_db):
    """
    Extracts modules, classes, functions, and methods FQNs from a Python file.
    Populates python_symbols_db.
    """
    if not current_module_fqn: # Cannot process if module FQN is not determined
        return

    try:
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()
        tree = ast.parse(content, filename=file_path)
    except Exception as e:
        # print(f"SyntaxError or other parsing error in {file_path} for symbol extraction: {e}")
        return

    # Add module symbol itself
    python_symbols_db[current_module_fqn] = {
        "type": "module",
        "module_fqn": current_module_fqn, # The module FQN is itself
        "file_path": file_path,
    }

    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            class_fqn = f"{current_module_fqn}.{node.name}"
            python_symbols_db[class_fqn] = {
                "type": "class",
                "module_fqn": current_module_fqn,
                "file_path": file_path,
            }
            # Extract methods within this class
            for sub_node in node.body:
                if isinstance(sub_node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    method_fqn = f"{class_fqn}.{sub_node.name}"
                    python_symbols_db[method_fqn] = {
                        "type": "method",
                        "module_fqn": current_module_fqn,
                        "file_path": file_path,
                    }
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            # Check if it's a top-level function (parent is ast.Module)
            # To find the parent, we'd need to parse the tree differently or pass parent info.
            # For simplicity, we assume if it's not inside a class_def already handled, it's module-level.
            # This check is imperfect if functions are nested inside other functions, but okay for typical structures.
            is_top_level_function = True
            # A more robust check would involve tracking the parent of each node during traversal.
            # For now, if we encounter a FunctionDef not inside a ClassDef loop above, assume it's module-level.
            # This needs refinement if there are nested functions that shouldn't be FQN'd this way.
            # A simple way to check: iterate tree.body directly for functions/classes.
            
            # Let's refine this: only add if it's a direct child of the module
            # This means we only iterate through tree.body for top-level functions.
            # The current ast.walk() approach will find all functions. We need to distinguish.

            # Re-thinking: The current walk processes nodes. If a FunctionDef node's direct parent is Module,
            # it is a top-level function. If direct parent is ClassDef, it is a method (handled above).
            # ast.walk doesn't give parent. We can iterate tree.body for top-level items.

            pass # Will be handled by a targeted loop below for module-level items.

    # Explicitly iterate top-level items for functions (methods are handled within class processing)
    for node in tree.body: # Iterate only direct children of the module node
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            function_fqn = f"{current_module_fqn}.{node.name}"
            if function_fqn not in python_symbols_db: # Avoid re-adding if it's somehow a method of a class named like the module
                python_symbols_db[function_fqn] = {
                    "type": "function",
                    "module_fqn": current_module_fqn,
                    "file_path": file_path,
                }


# --- New Function: Markdown File Discovery ---
def discover_markdown_files(repo_path):
    """Finds all .md and .markdown files in the repository."""
    md_files = []
    abs_repo_path = os.path.abspath(repo_path)
    for root, _, files in os.walk(abs_repo_path):
        for file in files:
            if file.endswith(".md") or file.endswith(".markdown"):
                md_files.append(os.path.join(root, file))
    return md_files

# --- New Function: Analyze Markdown File References ---
def analyze_markdown_file_references(md_file_path, all_python_fqns, python_symbols_db):
    """
    Analyzes a Markdown file to find references to Python symbols.
    Returns a set of module FQNs that are referenced in the Markdown file.
    """
    referenced_module_fqns_in_md = set()
    try:
        with open(md_file_path, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()
    except Exception as e:
        # print(f"Error reading Markdown file {md_file_path}: {e}")
        return referenced_module_fqns_in_md

    for py_fqn in all_python_fqns:
        # Escape FQN for regex and use word boundaries
        # Dots in FQNs need to be escaped for regex literal matching.
        escaped_py_fqn = re.escape(py_fqn)
        try:
            # Search for the FQN as a whole word
            if re.search(r'\b' + escaped_py_fqn + r'\b', content):
                # If a symbol FQN is found, get its parent module FQN from python_symbols_db
                symbol_info = python_symbols_db.get(py_fqn)
                if symbol_info and "module_fqn" in symbol_info:
                    referenced_module_fqns_in_md.add(symbol_info["module_fqn"])
                # If the py_fqn itself is a module FQN (already in python_symbols_db with type module)
                elif py_fqn in python_symbols_db and python_symbols_db[py_fqn]["type"] == "module":
                    referenced_module_fqns_in_md.add(py_fqn)

        except re.error as re_err:
            # print(f"Regex error for FQN '{py_fqn}' (escaped: '{escaped_py_fqn}'): {re_err}")
            pass # Skip this FQN if regex is invalid for some reason

    return referenced_module_fqns_in_md


# --- Output Functions for Markdown ---
def print_markdown_ranks_console(sorted_markdown_ranks, top_n):
    """Prints ranked Markdown files to the console."""
    print("\n--- Markdown File Ranks ---")
    if not sorted_markdown_ranks:
        print("No Markdown files to display or no references found.")
        return

    num_to_display = len(sorted_markdown_ranks)
    if top_n > 0:
        num_to_display = min(top_n, len(sorted_markdown_ranks))
    
    display_ranks = sorted_markdown_ranks[:num_to_display]

    if not display_ranks:
        # This can happen if top_n is 0, or no ranks but was handled above.
        if top_n == 0:
            print("(Markdown rank display to console suppressed by --top_n 0)")
        return # Already printed no ranks if that was the case

    max_path_len = 0
    if display_ranks:
        max_path_len = max(len(path) for path, _ in display_ranks)
    
    header_path = 'Markdown File Path'
    header_score = 'Score'
    if max_path_len < len(header_path):
        max_path_len = len(header_path)

    if top_n > 0 and len(sorted_markdown_ranks) > top_n:
        print(f"(Displaying Top {num_to_display} of {len(sorted_markdown_ranks)} Markdown files on console)")
    
    print(f"{header_path.ljust(max_path_len)} | {header_score}")
    print(f"{'-' * max_path_len} | {'-' * len(header_score)}")
    for path, score in display_ranks:
        print(f"{path.ljust(max_path_len)} | {score:.6f}")

def append_markdown_ranks_to_file(output_file_path, sorted_markdown_ranks):
    """Appends ranked Markdown files to the output file."""
    if not output_file_path: # Should not happen if called correctly
        return
    
    try:
        with open(output_file_path, 'a', encoding='utf-8') as f_out: # Append mode
            f_out.write("\n\n--- Markdown File Ranks ---\n")
            if not sorted_markdown_ranks:
                f_out.write("No Markdown files to rank or no references found.\n")
                return

            max_path_len = 0
            if sorted_markdown_ranks: # Calculate based on ALL ranks for file output
                 max_path_len = max(len(path) for path, _ in sorted_markdown_ranks)
            
            header_path = 'Markdown File Path'
            header_score = 'Score'
            if max_path_len < len(header_path):
                max_path_len = len(header_path)

            f_out.write(f"{header_path.ljust(max_path_len)} | {header_score}\n")
            f_out.write(f"{'-' * max_path_len} | {'-' * len(header_score)}\n")
            for path, score in sorted_markdown_ranks: # Write all ranks
                f_out.write(f"{path.ljust(max_path_len)} | {score:.6f}\n")
        # print(f"Successfully appended Markdown ranks to {output_file_path}") # Optional success message for this part
    except IOError as e:
        print(f"Error appending Markdown ranks to output file {output_file_path}: {e}")

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
                 output_file_path, top_n_arg, analyze_markdown):
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
    python_symbols_db = {} # Stores FQNs for modules, classes, functions, methods

    print("Mapping files to module names and extracting Python symbols...")
    for f_path in all_py_files:
        fqn = path_to_module_fqn(f_path, abs_repo_path)
        if fqn: # path_to_module_fqn can return None for e.g. repo_root/__init__.py
            module_map[fqn] = f_path
            file_to_fqn[f_path] = fqn
            internal_module_fqns.add(fqn)
            # Extract symbols for this file
            extract_python_symbols(f_path, fqn, abs_repo_path, python_symbols_db)
    
    print(f"Found {len(internal_module_fqns)} unique internal modules.")
    print(f"Extracted {len(python_symbols_db)} Python symbols (modules, classes, functions, methods).")

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

                    f_out.write(f"{file_header_module.ljust(file_max_name_len)} | {file_header_score}\n")
                    f_out.write(f"{'-' * file_max_name_len} | {'-' * len(file_header_score)}\n")
                    for module_fqn, rank in sorted_ranks: # Write all ranks to file
                        f_out.write(f"{module_fqn.ljust(file_max_name_len)} | {rank:.6f}\n")

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

    # --- Markdown Analysis (Conditional) ---
    if analyze_markdown:
        print("\n--- Analyzing Markdown Files ---")
        # 1. Discover Markdown files
        md_files = discover_markdown_files(abs_repo_path)
        if not md_files:
            print("No Markdown files found.")
        else:
            print(f"Found {len(md_files)} Markdown files.")

            # 2. Python symbols are in python_symbols_db.keys(). Modules FQNs also in internal_module_fqns.
            # We will primarily use python_symbols_db which contains modules, classes, functions, methods.
            all_py_fqns_for_md_search = set(python_symbols_db.keys())
            
            # 3. Analyze Markdown file references
            markdown_to_referenced_modules = defaultdict(set)
            print("Analyzing Markdown file references to Python symbols...")
            for md_file in md_files:
                referenced_modules = analyze_markdown_file_references(md_file, all_py_fqns_for_md_search, python_symbols_db)
                if referenced_modules:
                    markdown_to_referenced_modules[md_file].update(referenced_modules)
            
            # 4. Rank Markdown files
            markdown_file_scores = {}
            if markdown_to_referenced_modules:
                print("Ranking Markdown files based on referenced Python module CodeRanks...")
                for md_file, referenced_mods in markdown_to_referenced_modules.items():
                    score = sum(code_ranks.get(mod_fqn, 0) for mod_fqn in referenced_mods)
                    markdown_file_scores[md_file] = score
                
                sorted_markdown_ranks = sorted(markdown_file_scores.items(), key=lambda item: item[1], reverse=True)
                
                # 5. Output Markdown ranks
                print_markdown_ranks_console(sorted_markdown_ranks, top_n_arg)
                if output_file_path:
                    append_markdown_ranks_to_file(output_file_path, sorted_markdown_ranks)

                if not sorted_markdown_ranks:
                    print("No Markdown files to rank (all scores might be zero or no references found).")
                elif top_n_arg > 0 and output_file_path:
                    # Append top N Markdown file contents to the output file
                    print(f"Appending top {min(top_n_arg, len(sorted_markdown_ranks))} Markdown file contents to {output_file_path}...")
                    try:
                        with open(output_file_path, 'a', encoding='utf-8') as f_out:
                            f_out.write(f"\n\n--- Top {top_n_arg} Ranked Markdown File Contents ---\n")
                            
                            md_files_copied_count = 0
                            for md_path, md_score in sorted_markdown_ranks:
                                if md_files_copied_count >= top_n_arg:
                                    break
                                
                                f_out.write(f"\n\n--- START MARKDOWN FILE ({os.path.basename(md_path)} | Score: {md_score:.6f}): {md_path} ---\n\n")
                                try:
                                    with open(md_path, 'r', encoding='utf-8', errors='ignore') as f_md:
                                        f_out.write(f_md.read())
                                    f_out.write(f"\n--- END MARKDOWN FILE: {md_path} ---\n")
                                    md_files_copied_count += 1
                                except Exception as e:
                                    f_out.write(f"\n!!! Error reading Markdown file {md_path}: {e} !!!\n")
                            print(f"Successfully appended top {md_files_copied_count} Markdown file contents to {output_file_path}")
                    except IOError as e:
                        print(f"Error appending Markdown file contents to output file {output_file_path}: {e}")

            else:
                print("No Python references found in Markdown files, skipping Markdown ranking.")
                sorted_markdown_ranks = [] # Ensure it exists for potential output functions

            if not markdown_to_referenced_modules and md_files:
                 print("No Python symbol references found in any Markdown files.")

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
    parser.add_argument("--output_file", type=str, default=None,
                        help="File to write results to. Defaults to '{repo_name}_coderank_results.txt'.")
    parser.add_argument("--top_n", type=int, default=20,
                        help="Number of top modules/markdown files to print to console and whose contents to copy to the output file. (Default: 20)")
    parser.add_argument("--analyze_markdown", action='store_true',
                        help="Enable analysis of Markdown files to rank them based on Python code references. (Default: False)")
    args = parser.parse_args()

    output_file_path = args.output_file
    if output_file_path is None:
        repo_folder_name = os.path.basename(os.path.abspath(args.repo_path))
        output_file_path = f"{repo_folder_name}_coderank_results.txt"

    analyze_repo(args.repo_path, args.external_modules,
                 args.damping_factor, args.weight_internal,
                 args.weight_external_import, args.weight_external_dependency,
                 output_file_path, args.top_n, args.analyze_markdown)

if __name__ == "__main__":
    main()