#!/bin/bash
set -e  # Exit immediately if a command exits with a non-zero status

# Upgrade pip and install poetry
pip install --upgrade pip
pip install poetry

# Update the lock file if necessary
poetry lock

# Install dependencies and the project
poetry install

# Create and install the IPython kernel for the project
python -m ipykernel install --user --name=coderank --display-name "CodeRank" # install globally outside of poetry

echo "Jupyter kernel 'coderank' has been installed."

# pip install -e .

echo "Project setup complete!"