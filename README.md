# Cellular Automata PEG Generator

This project uses a 1D Cellular Automaton to generate random text, which is parsed by a custom Packrat PEG Parser into an AST, and transpiled into Python, C, C++, Go, and Rust.

## Usage
python generator.py python  # Uses built-in simplified Python grammar
python generator.py rust    # Uses built-in simplified Rust grammar
python generator.py go      # Uses built-in simplified Go grammar
