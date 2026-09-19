#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""EnvSwitch 启动入口（开发模式）：python main.py"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from envswitch.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
