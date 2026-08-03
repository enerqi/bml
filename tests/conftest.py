import os
import sys

# the bml tools are flat modules at the repo root, not an installed package
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
