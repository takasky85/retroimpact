#!/usr/bin/env python3
"""Launch RetroImpact:  python run_retroimpact.py [file.prn]   (or: python -m retroimpact ...)"""
import sys

from retroimpact.cli import main

sys.exit(main())
