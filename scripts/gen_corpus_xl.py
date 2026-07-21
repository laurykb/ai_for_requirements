#!/usr/bin/env python
"""Point d'entrée : génère lynx/corpus/corpus_xl.json.

Usage :  .venv/bin/python scripts/gen_corpus_xl.py [--target 350] [--stride 2] [--no-llm]
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lynx"))
from src.corpus_gen import main  # noqa: E402

if __name__ == "__main__":
    main()
