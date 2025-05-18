# Plan: Extend CodeRank to Analyze and Rank Markdown Files

## 1. Goal

Extend the `coderank.py` script to identify and rank Markdown files (`.md`, `.markdown`) within a repository. The ranking should be based on how frequently and significantly these Markdown files reference Python code elements (modules, classes, functions, methods) from the codebase. Markdown files that document or discuss important/central Python modules should receive a higher rank.

## 2. Phases of Implementation

### Phase 2.1: Enhanced Python Code Analysis (Symbol Extraction)

The existing script already identifies Python modules. We need to enhance this to extract more granular symbols.

*   **Objective:** Collect Fully Qualified Names (FQNs) for all modules, classes, functions, and methods.
*   **New Function:** `extract_python_symbols(file_path, current_module_fqn)` (or modify existing ones).
    *   This function will use `ast.parse` to build an Abstract Syntax Tree for each Python file.
    *   It will traverse the AST to find:
        *   `ast.ClassDef` nodes: Extract class names. FQN: `module_fqn.ClassName`.
        *   `ast.FunctionDef` and `ast.AsyncFunctionDef` nodes:
            *   If top-level in a module: Extract function names. FQN: `module_fqn.function_name`.
            *   If inside a `ast.ClassDef` (i.e., methods): Extract method names. FQN: `module_fqn.ClassName.method_name`.
*   **Data Structure for Python Symbols:**
    A dictionary to store information about each symbol, crucial for linking Markdown references back to Python code.
    ```python
    # Example:
    python_symbols_db = {
        "my_package.utils.helper_function": {
            "type": "function",
            "module_fqn": "my_package.utils",
            "file_path": "/path/to/repo/my_package/utils.py",
            # Potentially: "coderank_score_of_module": 0.8 (populated later)
        },
        "my_package.core.MainClass": {
            "type": "class",
            "module_fqn": "my_package.core",
            "file_path": "/path/to/repo/my_package/core.py",
        },
        "my_package.core.MainClass.process_data": {
            "type": "method",
            "module_fqn": "my_package.core", # The module containing the class
            "file_path": "/path/to/repo/my_package/core.py",
        }
    }
    ```
    *   This `python_symbols_db` will be populated alongside `module_map` and `internal_module_fqns`.

### Phase 2.2: Markdown File Discovery and Analysis

*   **Objective:** Find all Markdown files and identify which Python symbols they reference.
*   **New Function:** `discover_markdown_files(repo_path)`
    *   Similar to `discover_python_files`, this will walk the repository and find all files ending with `.md` or `.markdown`.
    *   Returns a list of absolute paths to these Markdown files.
*   **New Function:** `analyze_markdown_file_references(md_file_path, all_python_fqns, module_coderanks)`
    *   `md_file_path`: Path to the Markdown file to analyze.
    *   `all_python_fqns`: A set or list of all collected Python FQNs (from `python_symbols_db.keys()`).
    *   `module_coderanks`: The dictionary containing the CodeRank scores for Python modules (e.g., `coderanks` variable).
    *   **Steps:**
        1.  Read the content of the Markdown file.
        2.  Initialize `referenced_module_fqns_in_md = set()`.
        3.  For each `py_fqn` in `all_python_fqns`:
            *   Construct a regular expression to search for this FQN. Use word boundaries (`\\b`) to avoid partial matches (e.g., `\\bmy_function\\b` shouldn't match `my_function_extended`).
            *   Search for `py_fqn` in the Markdown content.
            *   Consider searching for just the base name too (e.g., `helper_function` from `my_package.utils.helper_function`). This adds complexity in disambiguation if base names are not unique across the codebase.
                *   *Initial Simplification:* Primarily focus on matching full FQNs or module_fqn.ClassName, module_fqn.function_name.
            *   If a match is found:
                *   Determine the module FQN to which this `py_fqn` belongs (e.g., from `python_symbols_db[py_fqn]["module_fqn"]`).
                *   Add this `module_fqn` to `referenced_module_fqns_in_md`.
        4.  Return `referenced_module_fqns_in_md`.
*   **Data Structure for Markdown References:**
    ```python
    # Stores which Python *modules* are referenced by each MD file
    markdown_to_referenced_modules = defaultdict(set)
    # md_file_path -> {module_fqn1, module_fqn2, ...}
    ```

### Phase 2.3: Ranking Markdown Files

*   **Objective:** Calculate a score for each Markdown file based on the CodeRank of the Python modules it references.
*   **Logic:**
    1.  After the Python module CodeRanks are calculated (`code_ranks` dictionary).
    2.  Initialize `markdown_file_scores = {}`.
    3.  For each `md_file_path` found by `discover_markdown_files`:
        *   Call `referenced_modules = analyze_markdown_file_references(md_file_path, python_symbols_db.keys(), code_ranks)`.
        *   Store these references: `markdown_to_referenced_modules[md_file_path] = referenced_modules`.
        *   Calculate the Markdown file's score:
            `score = sum(code_ranks.get(mod_fqn, 0) for mod_fqn in referenced_modules)`
            *   This means a Markdown file referencing highly-ranked Python modules will itself get a higher score.
            *   Consider a weighting factor if desired, e.g., `score *= md_reference_weight`.
        *   Store the score: `markdown_file_scores[md_file_path] = score`.
    4.  Sort `markdown_file_scores` by score in descending order.

### Phase 2.4: Output and Reporting

*   **Objective:** Display ranked Markdown files in console and output file.
*   **Console Output:**
    *   Add a new section: `--- Markdown File Ranks ---`.
    *   Print a table of ranked Markdown files (Path and Score), similar to Python modules.
    *   The `--top_n` argument could apply here, or a new `--top_n_md` could be introduced. For simplicity, initially, `--top_n` could apply to both Python and Markdown ranked lists.
*   **File Output (`--output_file`):**
    *   Append the ranked Markdown file list (all of them, not just top N) to the output file.
    *   Format similarly to the Python module ranks.
    *   Decide if content of top N Markdown files should be copied.
        *   *Initial Simplification:* Do not copy Markdown content to the output file to keep complexity down. Just list the ranks.

### Phase 2.5: Command-Line Interface (CLI) Changes

*   **Objective:** Allow users to control Markdown analysis.
*   **New `argparse` arguments:**
    *   `--analyze_markdown` or `--process_markdown`: A boolean flag (e.g., `action='store_true'`) to enable/disable this entire feature. Default to `False` initially.
    *   (Optional Future Enhancement) `--markdown_reference_weight`: A float to adjust the impact of Python references on Markdown scores.
*   **Logic in `main()` and `analyze_repo()`:**
    *   The new Markdown processing steps should be conditional on the `--analyze_markdown` flag.

## 3. Modifications to `coderank.py` Structure

*   **Global Data Structures:**
    *   `python_symbols_db`: To store FQNs of all functions, classes, methods and their parent modules.
    *   `markdown_to_referenced_modules`: To link Markdown files to the Python modules they reference.
    *   `markdown_file_scores`: To store the final scores for Markdown files.
*   **New Functions:**
    *   `extract_python_symbols(...)` (integrated into the Python file parsing loop).
    *   `discover_markdown_files(...)`
    *   `analyze_markdown_file_references(...)`
*   **Updates to `analyze_repo(...)`:**
    *   Call Python symbol extraction.
    *   If `--analyze_markdown` is enabled:
        *   Call `discover_markdown_files`.
        *   Loop through Markdown files, call `analyze_markdown_file_references`.
        *   Calculate and sort Markdown file scores.
        *   Include Markdown results in console and file outputs.
*   **Updates to `main()`:**
    *   Add new argparse arguments.
    *   Pass new arguments to `analyze_repo`.

## 4. Key Considerations & Challenges

*   **Symbol Disambiguation:**
    *   A simple function name like `calculate()` might appear in multiple modules.
    *   Prioritize matching full FQNs (e.g., `my_package.module.calculate`).
    *   If matching by base name (e.g., `calculate()`), a strategy is needed if it's ambiguous. For V1, we might only count a base name match if it uniquely maps to one FQN, or only count full FQN matches.
    *   The current plan leans towards linking MD files to *modules* rather than specific symbols within modules for scoring, which simplifies this: if `any_symbol` from `module_A` is mentioned, `module_A`'s rank contributes.
*   **Matching Granularity in Markdown:**
    *   Should `MyClass.my_method` in Markdown count as a reference to `my_module.MyClass.my_method`? Yes.
    *   Regex patterns need to be robust: `\b(my_package\.utils\.helper_function|ClassName\.method_name|function_name)\b`.
*   **Performance:**
    *   Extracting all symbols from all Python files will add to upfront processing time.
    *   Searching each Markdown file for a potentially large list of Python FQNs could be slow.
        *   Pre-compile regex patterns.
        *   Consider optimizing the search (e.g., Aho-Corasick algorithm if many patterns, but simple regex iteration might be fine for moderate numbers).
*   **Accuracy of References:**
    *   Plain text matching might find coincidental matches (e.g., a common word that's also a function name). Word boundaries (`\b`) help but aren't foolproof.
    *   Distinguishing between actual code references and general discussion of a term can be hard. This plan assumes any match is a potential reference.
*   **Defining "Reference":**
    *   If `module_a.py` contains `func1`, and `doc.md` mentions `module_a.func1`, this is a clear reference.
    *   If `doc.md` mentions `func1`, and `func1` is only defined in `module_a.py`, it's a strong implicit reference.
    *   If `doc.md` mentions `func1` and it's in `module_a.py` and `module_b.py`, it's ambiguous. The proposed scoring (sum of ranks of *modules* containing referenced symbols) handles this by potentially giving credit to both if we can map `func1` to both, or by only crediting if a FQN is used.
    *   Current plan: A Markdown file gets points from a Python module if *any* symbol FQN from that module (or the module FQN itself) is found in the Markdown.

## 5. Step-by-Step Integration into `analyze_repo`

1.  **After Python file discovery & initial FQN mapping (current state):**
    *   Loop through Python files again (or enhance the first pass) with `extract_python_symbols` to populate `python_symbols_db`. This gives all class/function/method FQNs.
2.  **Python PageRank Calculation (current state):**
    *   This proceeds as is, resulting in `code_ranks` for modules.
3.  **Markdown Analysis (New Section, if `args.analyze_markdown`):**
    *   `md_files = discover_markdown_files(repo_path)`
    *   `all_py_fqns = set(python_symbols_db.keys())` (and include module FQNs themselves: `internal_module_fqns`)
    *   For each `md_file` in `md_files`:
        *   `referenced_modules = analyze_markdown_file_references(md_file, all_py_fqns, code_ranks)`
        *   `markdown_to_referenced_modules[md_file].update(referenced_modules)`
        *   `md_score = sum(code_ranks.get(mod_fqn, 0) for mod_fqn in referenced_modules)`
        *   `markdown_file_scores[md_file] = md_score`
    *   `sorted_markdown_ranks = sorted(markdown_file_scores.items(), key=lambda item: item[1], reverse=True)`
4.  **Output (Modified):**
    *   Print Python module ranks (current state).
    *   If `args.analyze_markdown`:
        *   Print Markdown file ranks.
    *   Write Python module ranks to file (current state).
    *   If `args.analyze_markdown`:
        *   Write Markdown file ranks to file.

This detailed plan provides a roadmap for the implementation.
