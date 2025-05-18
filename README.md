**How to Run:**

1.  Install dependencies:
    ```bash
    sh project_setup.sh
    ```
2.  Run the project:
    ```bash
    poetry run python src/coderank.py /path/to/your/python_project_you_want_to_analyze
    ```

    **Example with custom external modules and weights:**
    ```bash
    python coderank.py /path/to/your/python_project \
           --external_modules "fastapi,pydantic,sqlalchemy" \
           --damping_factor 0.9 \
           --weight_external_dependency 0.7
    ```

**Explanation of Weights:**

* `--weight_internal` (default 1.0): Standard weight for links between modules within your repository.
* `--weight_external_import` (default 0.5): When an internal module `A` imports an external module `E` (`A -> E` in `G_imports`), this weight applies. A lower value means external modules "absorb" less rank from your internal modules.
* `--weight_external_dependency` (default 0.5): When an internal module `A` imports an external module `E`, this creates a conceptual link `E -> A` in the `G_imported_by` graph. This weight applies to that link. A lower value means importing an external module gives a smaller "boost" to module `A`'s "importing activity" score compared to importing an internal module. This directly addresses the requirement: "...this should contribute less than the intra-repo imports."
